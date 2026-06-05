"""Confirm-over-Telegram proposals (V27).

A side effect AIfred wants to take but isn't sure about (e.g. a calendar event
parsed from a self-note) is recorded as a PROPOSAL and pushed to Telegram with
Approve/Reject buttons. Nothing touches the calendar until the owner taps ✅, so
a hallucinated date can never land silently (respects V3/V7).

Calendar events carry a link back to the brain.md fragment they came from, so
the owner has the source context one tap away.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

log = logging.getLogger("aifred.proposals")

# Warsaw is +01:00 winter / +02:00 summer. Good enough for owner-local stamps.
TZ_OFFSET = "+02:00"


def brain_web_link(web_url: str, mcp_url: str, path: str, anchor: str = "") -> str:
    """Human URL for a brain.md note (best-effort). web_url overrides; else derive
    from the MCP url by dropping the trailing /mcp."""
    base = (web_url or re.sub(r"/mcp/?$", "", mcp_url or "")).rstrip("/")
    if not base or not path:
        return ""
    link = f"{base}/{path.lstrip('/')}"
    if anchor:
        link += f"#{anchor}"
    return link


EXTRACT_PROMPT = (
    "Wyłuskaj z notatki wydarzenia do kalendarza. Dziś jest {today}. Dla każdego "
    "podaj: summary (krótki tytuł), date (YYYY-MM-DD), all_day (true jeśli bez "
    "konkretnej godziny), start_time i end_time (HH:MM, tylko gdy all_day=false), "
    "evidence (DOSŁOWNY fragment notatki, z którego to wynika — żeby było wiadomo "
    "skąd i dlaczego). Uwzględnij rok {year} jeśli nie podano. "
    "Pomiń rzeczy bez KONKRETNEJ daty dziennej — same nazwy dni typu „w piątek”, "
    "„w przyszłym tygodniu” bez podanej daty POMIŃ, NIE zgaduj daty. "
    "NIE wymyślaj — używaj tylko treści notatki. "
    "Odpowiedz TYLKO tablicą JSON, np. "
    '[{{"summary":"Dentysta Zosia","date":"2026-06-09","all_day":false,'
    '"start_time":"16:40","end_time":"17:40",'
    '"evidence":"Zosia 09.06 wizyta u dentysty STOMATOLOG KAMIENNA 21 16.40"}}]. '
    "Pusta tablica [] jeśli brak wydarzeń."
)


def extract_events(llm, text: str, today: str) -> list[dict]:
    year = today[:4]
    sys = EXTRACT_PROMPT.format(today=today, year=year)
    try:
        raw = llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": text[:2000]}]).content
        data = json.loads(raw)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        if isinstance(d, dict) and d.get("summary") and d.get("date"):
            out.append(d)
    return out[:20]


def _rfc3339(date: str, hm: str) -> str:
    return f"{date}T{hm}:00{TZ_OFFSET}"


def _next_hm(hm: str) -> str:
    try:
        h, m = (int(x) for x in hm.split(":"))
        return f"{(h + 1) % 24:02d}:{m:02d}"
    except ValueError:
        return hm


def event_to_payload(ev: dict, source: str, context_link: str) -> dict:
    """Calendar args from an extracted event. All-day -> date strings; timed ->
    RFC3339. Description carries the evidence (why) + a link to the source note."""
    date = str(ev["date"])
    evidence = str(ev.get("evidence") or ev.get("note", "")).strip()
    desc = f"Powód: {evidence}" if evidence else ""
    if context_link:
        desc = (desc + f"\n\nKontekst: {context_link}").strip()
    if ev.get("all_day") or not ev.get("start_time"):
        from datetime import datetime, timedelta

        try:
            nxt = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            nxt = date
        start, end = date, nxt  # all-day (end exclusive)
    else:
        st = str(ev["start_time"])
        en = str(ev.get("end_time") or _next_hm(st))
        start, end = _rfc3339(date, st), _rfc3339(date, en)
    return {"summary": str(ev["summary"])[:120], "start": start, "end": end,
            "source": source, "description": desc}


def _summary_line(ev: dict) -> str:
    when = ev["date"] + ("" if ev.get("all_day") or not ev.get("start_time") else f" {ev['start_time']}")
    return f"🗓️ {when} — {ev['summary']}"


def propose_events(store, llm, text: str, today: str, source: str, context_link: str, batch_ref: str) -> list[dict]:
    """Extract events from `text`, store each as a pending proposal (deduped).
    Returns the new proposals as {id, line, evidence, link} for Telegram."""
    events = extract_events(llm, text, today)
    made = []
    for ev in events:
        payload = event_to_payload(ev, source, context_link)
        ref = f"{batch_ref}:{hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10]}"
        line = _summary_line(ev)
        pid = store.add_proposal("calendar", json.dumps(payload), line, ref, today_ts(today))
        if pid:
            made.append({"id": pid, "line": line,
                         "evidence": str(ev.get("evidence") or ev.get("note", "")).strip(),
                         "link": context_link})
    return made


def today_ts(today: str) -> float:
    from datetime import datetime, timezone

    try:
        return datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


class CalendarProposalResolver:
    """Executes a proposal when the owner approves it from Telegram."""

    def __init__(self, store, calendar_service):
        self.store = store
        self.calendar = calendar_service

    def approve(self, pid: int) -> str:
        row = self.store.get_proposal(pid)
        if row is None:
            return "Nie znaleziono propozycji."
        if row["status"] != "pending":
            return f"Propozycja już: {row['status']}."
        if self.calendar is None:
            return "Kalendarz niedostępny."
        from aifred.google.tools import calendar_create

        try:
            payload = json.loads(row["payload"])
            res = calendar_create(self.calendar, **payload)
        except Exception as e:  # noqa: BLE001
            self.store.set_proposal_status(pid, "error")
            log.warning("proposal %s failed: %s", pid, e)
            return f"Błąd przy tworzeniu: {e}"
        self.store.set_proposal_status(pid, "done")
        link = res.get("htmlLink", "")
        return f"✅ Dodano: {row['summary']}" + (f"\n{link}" if link else "")

    def reject(self, pid: int) -> str:
        row = self.store.get_proposal(pid)
        if row is None:
            return "Nie znaleziono propozycji."
        if row["status"] == "pending":
            self.store.set_proposal_status(pid, "rejected")
        return f"❌ Odrzucono: {row['summary'] if row else pid}"

    # called by the telegram bot on a callback_data string "cal_ok:<id>" / "cal_no:<id>"
    def handle_callback(self, data: str) -> str | None:
        m = re.match(r"(cal_ok|cal_no):(\d+)", data or "")
        if not m:
            return None
        pid = int(m.group(2))
        return self.approve(pid) if m.group(1) == "cal_ok" else self.reject(pid)
