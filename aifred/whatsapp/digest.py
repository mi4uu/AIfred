"""WhatsApp digest (V11, V15). Code prefilters; LLM only summarizes shortlist.

Pipeline:
  1. prefilter() — pure code scoring (sender/keyword/question/date/length).
     Drops chatter; keeps actionable. This is the context-saver (V11): the LLM
     never sees the raw group history, only the ranked shortlist.
  2. summarize() — one LLM call over the shortlist -> {summary, items[]}.
  3. persist items to brain.md (V2) + store cache.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from aifred.whatsapp.ingest import WAMessage

IMPORTANT_KEYWORDS = {
    "urgent", "asap", "today", "tomorrow", "pay", "payment", "invoice", "deadline",
    "appointment", "doctor", "school", "meeting", "remember", "don't forget", "please",
    "can you", "need", "bring", "pick up", "call me",
}
_QUESTION_WORDS = ("when", "where", "what", "who", "how", "why", "can", "could", "would", "is", "are", "do")
_TIME_RE = re.compile(r"\b(\d{1,2}[:.]\d{2}|\d{1,2}\s?(am|pm)|mon|tue|wed|thu|fri|sat|sun)\b", re.I)
_DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}|today|tomorrow|tonight|next week)\b", re.I)

DEFAULT_SHORTLIST_MAX = 15


@dataclass
class ScoredMsg:
    msg: WAMessage
    score: int
    reasons: list[str] = field(default_factory=list)


def score_message(m: WAMessage, important_senders: set[str]) -> ScoredMsg:
    body = (m.body or "").lower()
    score = 0
    reasons: list[str] = []
    if m.sender in important_senders:
        score += 1  # amplifies, but won't solo-surface chatter (needs a content signal too)
        reasons.append("important-sender")
    hits = [k for k in IMPORTANT_KEYWORDS if k in body]
    if hits:
        score += len(hits)
        reasons.append(f"kw:{','.join(hits[:3])}")
    if "?" in body or body.strip().startswith(_QUESTION_WORDS):
        score += 2
        reasons.append("question")
    if _TIME_RE.search(body):
        score += 2
        reasons.append("time")
    if _DATE_RE.search(body):
        score += 2
        reasons.append("date")
    return ScoredMsg(msg=m, score=score, reasons=reasons)


def prefilter(
    messages: list[WAMessage],
    important_senders: set[str] | None = None,
    min_score: int = 2,
    limit: int = DEFAULT_SHORTLIST_MAX,
) -> list[ScoredMsg]:
    """Pure code (V15). Keep actionable msgs above threshold, top `limit`."""
    important_senders = important_senders or set()
    scored = [score_message(m, important_senders) for m in messages]
    kept = [s for s in scored if s.score >= min_score]
    kept.sort(key=lambda s: s.score, reverse=True)
    return kept[:limit]


def _shortlist_text(shortlist: list[ScoredMsg]) -> str:
    lines = []
    for s in shortlist:
        lines.append(f"- [{s.msg.sender}] {s.msg.body}")
    return "\n".join(lines)


DIGEST_PROMPT = (
    "These are pre-filtered important WhatsApp messages. Summarize what needs my "
    "attention and extract action items. Reply ONLY JSON: "
    '{"summary": str, "items": [{"kind": "todo|event|info", "content": str}]}'
)


def summarize(shortlist: list[ScoredMsg], llm) -> dict:
    """One LLM call over shortlist ONLY (V11). Returns parsed digest dict."""
    if not shortlist:
        return {"summary": "", "items": []}
    messages = [
        {"role": "system", "content": DIGEST_PROMPT},
        {"role": "user", "content": _shortlist_text(shortlist)},
    ]
    res = llm.chat(messages)
    try:
        data = json.loads(res.content)
    except (json.JSONDecodeError, AttributeError):
        return {"summary": res.content if hasattr(res, "content") else "", "items": []}
    data.setdefault("summary", "")
    data.setdefault("items", [])
    return data


def run_digest(
    messages: list[WAMessage],
    llm,
    brain=None,
    store=None,
    important_senders: set[str] | None = None,
    now_ts: float = 0.0,
    chat_id: str = "",
) -> dict:
    """Full pipeline: prefilter -> summarize -> persist items (V2)."""
    shortlist = prefilter(messages, important_senders)
    digest = summarize(shortlist, llm)
    for item in digest.get("items", []):
        line = f"[wa:{chat_id}] {item.get('kind', 'info')}: {item.get('content', '')}"
        if brain is not None:
            brain.append(line)  # V2 canonical
        if store is not None:
            store.add_item(f"whatsapp:{chat_id}", item.get("kind", "info"), item.get("content", ""), now_ts)
    digest["shortlist_size"] = len(shortlist)
    digest["scanned"] = len(messages)
    return digest
