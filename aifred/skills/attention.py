"""Attention tool — let the agent read the triage output (what needs attention).

Triage writes classified actionables to the store; without this tool the agent
can't see them and falls back to brain.md (missing "buy X" / "pick up package"
that came from mail/WhatsApp). Tagged 'core' so it's available for any
"what's important / today / anything to do" question.
"""

import re

from pydantic import BaseModel, Field

from aifred.store.db import Store
from aifred.tools.base import Tool, tool_from_model

_NUM = re.compile(r"\d{9,}")  # phone/jid-like runs in frozen triage text


def _resolve_identities(text: str, contacts) -> str:
    """Swap raw numbers in stored attention text for known names — fixes items
    triaged before the sender was linked (e.g. 48598765432 -> Kasia)."""
    if not contacts:
        return text
    def sub(m):
        nm = contacts.name_for(m.group(0))
        return nm if nm and nm != m.group(0) else m.group(0)
    return _NUM.sub(sub, text)


def attention_list(store: Store, importance: str | None = None, limit: int = 25, contacts=None) -> dict:
    rows = store.list_attention("open")
    items = []
    for r in rows:
        if importance and r["kind"] != importance:
            continue
        items.append({"id": r["id"], "importance": r["kind"], "text": _resolve_identities(r["content"], contacts)})
        if len(items) >= limit:
            break
    if not items:
        return {"items": [], "note": "nothing flagged for attention right now"}
    return {"items": items, "count": len(items)}


class AttentionArgs(BaseModel):
    importance: str | None = Field(default=None, description="filter: high/medium/low")
    limit: int = Field(default=25, ge=1, le=50)


def build_attention_tool(store: Store, contacts=None) -> list[Tool]:
    return [
        tool_from_model(
            "attention_list",
            "what currently needs attention (triaged mail + WhatsApp actionables)",
            AttentionArgs,
            lambda importance, limit: attention_list(store, importance, limit, contacts),
            tags=("core", "digest"),
        ),
    ]
