"""Important-digest (V11, V15).

Code aggregates compact structured signals from gmail + calendar + flagged
whatsapp items (store). The LLM gets only this small digest object — never raw
mail bodies or message history (V11). Aggregation is pure code (V15).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from aifred.google.tools import calendar_list, gmail_search
from aifred.store.db import Store
from aifred.tools.base import Tool, tool_from_model

UNREAD_MAX = 10
SUMMARY_PROMPT = (
    "Here is a compact digest of unread mail, today's events, and flagged "
    "messages. Write a short brief (max 6 lines) of what needs my attention. "
    "Plain text, no preamble."
)


def gather_signals(
    gmail_service,
    calendar_service,
    store: Store,
    time_min: str,
    time_max: str,
) -> dict:
    """Pure aggregation (V15) -> compact structured signals."""
    unread = gmail_search(gmail_service, "is:unread", max_results=UNREAD_MAX)
    events = calendar_list(calendar_service, time_min, time_max)
    flagged = [
        {"kind": r["kind"], "content": r["content"]} for r in store.list_items("open")
    ]
    return {
        "unread_count": len(unread),
        "unread": [{"from": u["from"], "subject": u["subject"]} for u in unread],
        "today_events": [{"summary": e["summary"], "start": e["start"]} for e in events],
        "flagged": flagged,
    }


def summarize_digest(signals: dict, llm) -> str:
    """One LLM call over the compact digest only (V11)."""
    payload = json.dumps(signals, ensure_ascii=False)
    res = llm.chat(
        [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": payload}]
    )
    return getattr(res, "content", "")


def daily_digest(gmail_service, calendar_service, store, llm, time_min: str, time_max: str) -> dict:
    signals = gather_signals(gmail_service, calendar_service, store, time_min, time_max)
    brief = summarize_digest(signals, llm)
    return {"brief": brief, "signals": signals}


class DigestArgs(BaseModel):
    time_min: str = Field(description="RFC3339 start of window (e.g. today 00:00)")
    time_max: str = Field(description="RFC3339 end of window (e.g. today 23:59)")


def build_digest_tool(gmail_service, calendar_service, store, llm) -> list[Tool]:
    return [
        tool_from_model(
            "daily_digest", "brief of unread mail + today's events + flagged messages",
            DigestArgs,
            lambda time_min, time_max: daily_digest(gmail_service, calendar_service, store, llm, time_min, time_max),
            tags=("digest",),
        ),
    ]
