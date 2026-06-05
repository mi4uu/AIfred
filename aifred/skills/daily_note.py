"""Daily note composer (V23) — a structured end-of-day summary in brain.md.

Teaches AIfred what a good "notatka z dnia" looks like: code gathers the day's
real signals (who wrote and what they wanted, what got done, what's still open,
calendar, mail worth noting), and the LLM fills a FIXED Polish section template
(code-first / LLM-last, V15). Empty sections are dropped. Owner's own messages
(from_me) and undirected group chatter are excluded — same noise filter as triage.

The canonical template also lives in brain.md (Journal/_szablon-dzienny.md) so the
agent can read the structure on demand; ensure_template() writes it once.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from aifred.mcp.brainmd import BrainMD
from aifred.skills.journal import journal_path
from aifred.tools.base import Tool, tool_from_model

TEMPLATE_PATH = "Journal/_szablon-dzienny.md"

# The structure AIfred must follow. Sections with no content are omitted.
NOTE_FORMAT = """# 📓 {date} ({weekday})

## ⭐ Najważniejsze
- (1–3 rzeczy, które naprawdę miały znaczenie tego dnia)

## 💬 Komunikacja
- **Imię:** czego chciał/a, o co prosił/a, co ustalono (tylko skierowane do mnie)

## 📌 Do zrobienia
- (otwarte prośby i zadania, które wypłynęły — z kontekstem)

## ✅ Zrobione
- (co zostało domknięte)

## 📅 Kalendarz
- (wydarzenia dnia)

## 🧠 Notatki
- (przemyślenia, kontekst wart zapamiętania)
"""

_WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def _weekday_pl(day: str) -> str:
    try:
        return _WEEKDAYS_PL[datetime.strptime(day, "%Y-%m-%d").weekday()]
    except ValueError:
        return ""


def _norm_ts(ts: float) -> float:
    """WhatsApp stores ms, mail/seconds — normalize to epoch seconds."""
    return ts / 1000.0 if ts > 1e12 else ts


def _day_bounds(day: str) -> tuple[float, float]:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    lo = d.timestamp()
    return lo, lo + 86400.0


def gather_day(store, contacts, day: str, limit: int = 800) -> dict:
    """Pull the day's real signals as structured data (no LLM). Communication is
    grouped by resolved person; owner's own + undirected group msgs are dropped."""
    lo, hi = _day_bounds(day)
    convo: dict[str, list[str]] = {}
    for r in store.recent_messages("whatsapp", limit=limit):
        if "from_me" in r.keys() and r["from_me"]:
            continue
        ts = _norm_ts(r["ts"])
        if not (lo <= ts < hi):
            continue
        body = (r["body"] or "").strip()
        if not body:
            continue
        if contacts and contacts.is_owner(r["sender"]):
            continue  # owner's own line (lid not flagged from_me on old rows)
        if r["is_group"]:
            continue  # group chatter isn't personal-daily material; triage-elevated
            # group items still arrive via the attention section below
        name = r["sender_name"] or r["sender"]
        if contacts:
            name = contacts.name_for(r["sender"], r["sender_name"] or "")
        convo.setdefault(name, []).append(body[:160])
    # attention items filed/seen that day (triage's view of what mattered)
    attention = []
    for status in ("open", "done"):
        for it in store.list_attention(status):
            ts = _norm_ts(it["created_ts"])
            if lo <= ts < hi and it["kind"] in ("high", "medium"):
                attention.append(f"[{it['kind']}] {it['content']}")
    return {"day": day, "communication": convo, "attention": attention}


def _signals_text(sig: dict) -> str:
    lines = []
    if sig["communication"]:
        lines.append("ROZMOWY (po osobie):")
        for who, msgs in sig["communication"].items():
            lines.append(f"- {who}: " + " | ".join(msgs))
    if sig["attention"]:
        lines.append("\nUWAGA WG TRIAGE:")
        lines.extend(f"- {a}" for a in sig["attention"])
    return "\n".join(lines).strip()


COMPOSE_PROMPT = (
    "Jesteś osobistym asystentem. Na podstawie surowych sygnałów z dnia napisz "
    "notatkę dzienną PO POLSKU, dokładnie w tym układzie sekcji:\n\n"
    "{fmt}\n\n"
    "Zasady: pisz zwięźle, konkretnie, w 1. osobie z perspektywy właściciela. "
    "Pomiń sekcję, dla której nie ma treści (nie wypisuj pustych nagłówków). "
    "Nie zmyślaj — używaj tylko podanych sygnałów. Nie dodawaj wstępu ani podsumowania."
)


def compose_daily_note(brain: BrainMD, llm, store, contacts, day: str, write: bool = True) -> dict:
    """Gather the day's signals -> LLM fills the fixed template -> brain.md (V2/V23)."""
    sig = gather_day(store, contacts, day)
    body = _signals_text(sig)
    weekday = _weekday_pl(day)
    if not body:
        return {"day": day, "written": False, "reason": "no signals"}
    sys = COMPOSE_PROMPT.format(fmt=NOTE_FORMAT.format(date=day, weekday=weekday))
    res = llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": body}])
    note = getattr(res, "content", "").strip()
    if not note:
        return {"day": day, "written": False, "reason": "empty"}
    if write:
        brain.write(journal_path(day), note)  # full-day note = one structured doc
    return {"day": day, "written": write, "note": note}


def ensure_template(brain: BrainMD) -> None:
    """Write the canonical daily-note template to brain.md once (reference)."""
    sample = NOTE_FORMAT.format(date="YYYY-MM-DD", weekday="dzień tygodnia")
    content = (
        "---\ntitle: 🗒️ Szablon notatki dziennej\ntags: [journal, szablon]\n---\n\n"
        "Tak ma wyglądać notatka z dnia (AIfred trzyma się tego układu; puste sekcje pomija):\n\n"
        + sample
    )
    try:
        brain.write(TEMPLATE_PATH, content)
    except Exception:  # noqa: BLE001
        pass


def _yesterday() -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


class DailyNoteArgs(BaseModel):
    day: str | None = Field(default=None, description="YYYY-MM-DD; domyślnie wczoraj")


def build_daily_note_tool(brain: BrainMD, llm, store, contacts) -> list[Tool]:
    ensure_template(brain)  # keep the canonical structure available in brain.md
    if llm is None:
        return []
    return [
        tool_from_model(
            "daily_note",
            "ułóż ustrukturyzowaną notatkę dzienną z rozmów/maili/triage i zapisz do brain.md",
            DailyNoteArgs,
            lambda day: compose_daily_note(brain, llm, store, contacts, day or _yesterday()),
            tags=("journal",),
        )
    ]
