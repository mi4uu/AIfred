"""Daily journal skill (V2, V6, V12) — backed by brain.md folders.

Layout (matches existing vault):
  Journal/YYYY-MM-DD.md   user-facing daily log (append-only entries, V6)
  hermes/                 internal-detail catalog (reasoning, raw notes)

distill_to_journal implements the hermes pattern: read the internal catalog via
RAG (scoped, V12), have the LLM draw conclusions, append them to today's
journal. Recall reads via context_for_query (V12), never the whole vault.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from aifred.mcp.brainmd import BrainMD
from aifred.tools.base import Tool, tool_from_model

JOURNAL_FOLDER = "Journal"
INTERNAL_FOLDER = "hermes"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_hm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def journal_path(day: str) -> str:
    return f"{JOURNAL_FOLDER}/{day}.md"


def journal_add(brain: BrainMD, text: str, day: str | None = None, time_str: str | None = None) -> str:
    day = day or _today()
    time_str = time_str or _now_hm()
    brain.append(f"- {time_str} {text}", path=journal_path(day))  # append-only (V6), canonical (V2)
    return f"journaled {day} {time_str}"


def journal_recall(brain: BrainMD, query: str, top_k: int = 5) -> list[str]:
    """Retrieve relevant journal chunks (V12), scoped to the Journal folder."""
    return brain.retrieve(query, top_k=top_k, scope=[JOURNAL_FOLDER])


DISTILL_PROMPT = (
    "Poniżej notatki wewnętrzne. Wypisz 1–4 zwięzłe punkty do dziennika po polsku, "
    "ujmując to co dla mnie ważne (decyzje, wydarzenia, rzeczy do zapamiętania). "
    "Czyste punkty, bez wstępu. Pełną notatkę dnia układa narzędzie daily_note."
)


def distill_to_journal(brain: BrainMD, llm, query: str, day: str | None = None, internal_scope: str = INTERNAL_FOLDER) -> dict:
    """Read internal catalog (scoped RAG, V12) -> LLM conclusions -> journal (V2)."""
    day = day or _today()
    context = brain.context(query, budget_tokens=1500, scope=[internal_scope])  # V12 scoped
    if not context.strip():
        return {"appended": 0, "day": day}
    res = llm.chat([{"role": "system", "content": DISTILL_PROMPT}, {"role": "user", "content": context}])
    conclusions = getattr(res, "content", "").strip()
    if not conclusions:
        return {"appended": 0, "day": day}
    brain.append(conclusions, path=journal_path(day))  # V2
    return {"appended": 1, "day": day, "conclusions": conclusions}


class JournalAddArgs(BaseModel):
    text: str = Field(description="what to record")
    day: str | None = Field(default=None, description="YYYY-MM-DD; default today")


class JournalRecallArgs(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class DistillArgs(BaseModel):
    query: str = Field(description="topic to pull from the internal catalog")
    day: str | None = Field(default=None, description="YYYY-MM-DD; default today")


def build_journal_tools(brain: BrainMD, llm=None) -> list[Tool]:
    tools = [
        tool_from_model(
            "journal_add", "append a dated entry to today's journal in brain.md", JournalAddArgs,
            lambda text, day: journal_add(brain, text, day), tags=("journal",),
        ),
        tool_from_model(
            "journal_recall", "search past journal entries in brain.md", JournalRecallArgs,
            lambda query, top_k: journal_recall(brain, query, top_k), tags=("journal",),
        ),
    ]
    if llm is not None:
        tools.append(
            tool_from_model(
                "journal_distill", "summarize internal notes into today's journal", DistillArgs,
                lambda query, day: distill_to_journal(brain, llm, query, day), tags=("journal",),
            )
        )
    return tools
