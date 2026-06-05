"""Calendar planning from brain.md (V3, V7, V12).

Read-only PROPOSER: retrieves relevant brain.md chunks (V12), asks the LLM to
extract candidate events, and stamps each with a source pointing back to the
brain content (V3 — no invented events). It never writes the calendar itself;
execution goes through calendar_create (side-effecting, confirm-gated, V7).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from aifred.mcp.brainmd import BrainMD
from aifred.tools.base import Tool, tool_from_model

PLAN_PROMPT = (
    "From these notes, extract calendar events to schedule. Reply ONLY JSON: "
    '{"proposals":[{"summary":str,"start":"RFC3339","end":"RFC3339"}]}. '
    "Only events explicitly implied by the notes; do not invent."
)


def propose_events(brain: BrainMD, llm, query: str, top_k: int = 5) -> dict:
    chunks = brain.retrieve(query, top_k=top_k)  # V12
    if not chunks:
        return {"proposals": []}
    notes = "\n---\n".join(chunks)
    res = llm.chat(
        [{"role": "system", "content": PLAN_PROMPT}, {"role": "user", "content": notes}]
    )
    try:
        data = json.loads(res.content)
    except (json.JSONDecodeError, AttributeError):
        return {"proposals": [], "raw": getattr(res, "content", "")}
    proposals = data.get("proposals", [])
    # V3: every proposal carries a source back to brain.md
    src = f"brain.md retrieve: {query!r}"
    for p in proposals:
        p["source"] = src
    return {"proposals": proposals, "note": "confirm before calendar_create (V7)"}


class CalPlanArgs(BaseModel):
    query: str = Field(description="what to look up in brain.md (e.g. 'this week plans')")
    top_k: int = Field(default=5, ge=1, le=20)


def build_calendar_plan_tools(brain: BrainMD, llm) -> list[Tool]:
    # proposer is read-only (not side_effecting); calendar_create does the write
    return [
        tool_from_model(
            "calendar_propose", "propose events from brain.md notes (read-only, cites source)",
            CalPlanArgs, lambda query, top_k: propose_events(brain, llm, query, top_k),
            tags=("calendar",),
        ),
    ]
