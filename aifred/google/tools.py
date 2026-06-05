"""Google tools (I.gmail, I.calendar). Typed, code-driven (V10, V15).

gmail = read-only (V4). calendar = read + create/update (side-effecting, gated
by confirm V7). Calendar writes require a `source` (V3: no invented events).
Bodies truncated before return so the LLM never eats a full thread (V11).

Functions take an injected `service` (googleapiclient resource) so tests run
without network. build_google_tools() binds live services into Tools.
"""

from __future__ import annotations

import base64
from typing import Any

from pydantic import BaseModel, Field

from aifred.tools.base import Tool, ToolError, tool_from_model

BODY_MAX = 2000  # V11: cap body chars returned to LLM


# ---------- gmail (read-only, V4) ----------

def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def gmail_search(service, query: str, max_results: int = 10) -> list[dict]:
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    out = []
    for m in resp.get("messages", []):
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["Subject", "From", "Date"])
            .execute()
        )
        p = msg.get("payload", {})
        out.append(
            {
                "id": msg["id"],
                "subject": _header(p, "Subject"),
                "from": _header(p, "From"),
                "date": _header(p, "Date"),
                "snippet": msg.get("snippet", ""),
                "internal_ts": int(msg.get("internalDate", 0)) / 1000.0,  # ms->s, for triage cursor
            }
        )
    return out


def _extract_body(payload: dict) -> str:
    if payload.get("body", {}).get("data"):
        raw = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
        return raw
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []):
        nested = _extract_body(part)
        if nested:
            return nested
    return ""


def gmail_get(service, message_id: str) -> dict:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    p = msg.get("payload", {})
    body = _extract_body(p)[:BODY_MAX]  # V11
    return {
        "id": msg["id"],
        "subject": _header(p, "Subject"),
        "from": _header(p, "From"),
        "date": _header(p, "Date"),
        "body": body,
    }


# ---------- calendar (read + write, V3) ----------

def calendar_list(service, time_min: str, time_max: str, max_results: int = 20, calendar_id: str = "primary") -> list[dict]:
    resp = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    out = []
    for e in resp.get("items", []):
        out.append(
            {
                "id": e.get("id"),
                "summary": e.get("summary", ""),
                "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
                "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
            }
        )
    return out


def _cal_endpoint(value: str) -> dict:
    """All-day (YYYY-MM-DD) -> {'date'}, timed (has 'T') -> {'dateTime'}."""
    return {"date": value} if len(value) == 10 and "T" not in value else {"dateTime": value}


def calendar_create(
    service, summary: str, start: str, end: str, source: str, description: str = "", calendar_id: str = "primary"
) -> dict:
    # V3: source mandatory; recorded in description for provenance
    if not source.strip():
        raise ToolError("calendar_create requires non-empty source (V3)")
    body = {
        "summary": summary,
        "start": _cal_endpoint(start),
        "end": _cal_endpoint(end),
        "description": f"{description}\n\n[aifred source: {source}]".strip(),
    }
    e = service.events().insert(calendarId=calendar_id, body=body).execute()
    return {"id": e.get("id"), "summary": e.get("summary"), "htmlLink": e.get("htmlLink")}


def calendar_update(
    service, event_id: str, source: str, summary: str | None = None, start: str | None = None,
    end: str | None = None, calendar_id: str = "primary",
) -> dict:
    if not source.strip():
        raise ToolError("calendar_update requires non-empty source (V3)")
    patch: dict[str, Any] = {}
    if summary is not None:
        patch["summary"] = summary
    if start is not None:
        patch["start"] = {"dateTime": start}
    if end is not None:
        patch["end"] = {"dateTime": end}
    patch["description"] = f"[aifred source: {source}]"
    e = service.events().patch(calendarId=calendar_id, eventId=event_id, body=patch).execute()
    return {"id": e.get("id"), "summary": e.get("summary")}


# ---------- arg models ----------

class GmailSearchArgs(BaseModel):
    query: str
    max_results: int = Field(default=10, ge=1, le=50)


class GmailGetArgs(BaseModel):
    message_id: str


class CalListArgs(BaseModel):
    time_min: str = Field(description="RFC3339 lower bound")
    time_max: str = Field(description="RFC3339 upper bound")
    max_results: int = Field(default=20, ge=1, le=100)


class CalCreateArgs(BaseModel):
    summary: str
    start: str = Field(description="RFC3339 start")
    end: str = Field(description="RFC3339 end")
    source: str = Field(description="origin of this event (brain.md ref or user msg) — required (V3)")
    description: str = ""


class CalUpdateArgs(BaseModel):
    event_id: str
    source: str = Field(description="why updated — required (V3)")
    summary: str | None = None
    start: str | None = None
    end: str | None = None


def build_google_tools(gmail_service, calendar_service) -> list[Tool]:
    """Bind live services into typed Tools (V10). Calendar writes side-effecting (V7)."""
    return [
        tool_from_model(
            "gmail_search", "search gmail, return id/subject/from/snippet", GmailSearchArgs,
            lambda query, max_results: gmail_search(gmail_service, query, max_results), tags=("gmail",),
        ),
        tool_from_model(
            "gmail_get", "read one gmail message (truncated body)", GmailGetArgs,
            lambda message_id: gmail_get(gmail_service, message_id), tags=("gmail",),
        ),
        tool_from_model(
            "calendar_list", "list calendar events in a time window", CalListArgs,
            lambda time_min, time_max, max_results: calendar_list(calendar_service, time_min, time_max, max_results),
            tags=("calendar",),
        ),
        tool_from_model(
            "calendar_create", "create a calendar event (needs source)", CalCreateArgs,
            lambda summary, start, end, source, description: calendar_create(
                calendar_service, summary, start, end, source, description
            ),
            side_effecting=True, tags=("calendar",),
        ),
        tool_from_model(
            "calendar_update", "update a calendar event (needs source)", CalUpdateArgs,
            lambda event_id, source, summary, start, end: calendar_update(
                calendar_service, event_id, source, summary, start, end
            ),
            side_effecting=True, tags=("calendar",),
        ),
    ]
