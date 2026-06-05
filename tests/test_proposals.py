"""Confirm-over-Telegram calendar proposals from self-notes (V27)."""

import json

from aifred.skills.proposals import (
    CalendarProposalResolver,
    brain_web_link,
    event_to_payload,
    extract_events,
    propose_events,
)
from aifred.store.db import Store


class FakeLLM:
    def __init__(self, events):
        self._events = events

    def chat(self, messages, tools=None, temperature=0.0):
        class R:
            content = json.dumps(self._events)

        return R()


def test_brain_web_link():
    assert brain_web_link("", "https://b.work/mcp", "Journal/inbox.md") == "https://b.work/Journal/inbox.md"
    assert brain_web_link("https://x.io", "", "a/b.md", anchor="h1") == "https://x.io/a/b.md#h1"
    assert brain_web_link("", "", "x.md") == ""


def test_event_to_payload_timed_and_allday():
    timed = event_to_payload(
        {"summary": "Dentysta", "date": "2026-06-09", "all_day": False, "start_time": "16:40"},
        "src", "https://b/n.md",
    )
    assert timed["start"] == "2026-06-09T16:40:00+02:00"
    assert timed["end"] == "2026-06-09T17:40:00+02:00"  # +1h default
    assert "Kontekst: https://b/n.md" in timed["description"]

    allday = event_to_payload({"summary": "Zosia u mnie", "date": "2026-06-16", "all_day": True}, "src", "")
    assert allday["start"] == "2026-06-16" and allday["end"] == "2026-06-17"  # date-only, end exclusive


def test_extract_events_filters_bad():
    llm = FakeLLM([
        {"summary": "Dentysta", "date": "2026-06-09", "start_time": "16:40"},
        {"summary": "", "date": "2026-06-10"},   # no title -> dropped
        {"note": "brak daty"},                    # no date -> dropped
    ])
    evs = extract_events(llm, "tekst", "2026-06-04")
    assert [e["summary"] for e in evs] == ["Dentysta"]


def test_propose_events_dedups_and_stores():
    s = Store(":memory:")
    llm = FakeLLM([{"summary": "Dentysta Zosia", "date": "2026-06-09", "start_time": "16:40"}])
    made = propose_events(s, llm, "Zosia 09.06 16:40 dentysta", "2026-06-04", "src", "https://b/n.md", "batch1")
    assert len(made) == 1 and made[0]["line"].startswith("🗓️ 2026-06-09 16:40")
    assert len(s.list_proposals("pending")) == 1
    # same batch_ref + same event -> deduped, no second proposal
    assert propose_events(s, llm, "Zosia 09.06 16:40 dentysta", "2026-06-04", "src", "https://b/n.md", "batch1") == []
    s.close()


class FakeCal:
    def __init__(self):
        self.created = []

    def events(self):
        return self

    def insert(self, calendarId=None, body=None):
        self.created.append(body)
        self._body = body

        class Exe:
            def execute(_):
                return {"id": "ev1", "summary": body["summary"], "htmlLink": "http://cal/ev1"}

        return Exe()


def test_resolver_approve_creates_event_with_context():
    s = Store(":memory:")
    payload = {"summary": "Dentysta", "start": "2026-06-09T16:40:00+02:00", "end": "2026-06-09T17:40:00+02:00",
               "source": "WA→ja (brain.md Journal/inbox.md)", "description": "Kontekst: https://b/n.md"}
    pid = s.add_proposal("calendar", json.dumps(payload), "🗓️ 2026-06-09 16:40 — Dentysta", "r1", 1.0)
    cal = FakeCal()
    res = CalendarProposalResolver(s, cal)
    out = res.approve(pid)
    assert "✅ Dodano" in out and len(cal.created) == 1
    assert "Kontekst: https://b/n.md" in cal.created[0]["description"]
    assert s.get_proposal(pid)["status"] == "done"
    # second approve is a no-op (already done)
    assert "już" in res.approve(pid)
    s.close()


def test_resolver_reject():
    s = Store(":memory:")
    pid = s.add_proposal("calendar", "{}", "x", "r2", 1.0)
    res = CalendarProposalResolver(s, None)
    assert "Odrzucono" in res.reject(pid)
    assert s.get_proposal(pid)["status"] == "rejected"
    s.close()


def test_resolver_handle_callback_routing():
    s = Store(":memory:")
    pid = s.add_proposal("calendar", "{}", "x", "r3", 1.0)
    res = CalendarProposalResolver(s, None)
    assert res.handle_callback("nonsense") is None
    assert "Odrzucono" in res.handle_callback(f"cal_no:{pid}")
    s.close()
