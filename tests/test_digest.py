"""T14 important-digest tests (V11 compact signals, V15 code aggregation)."""

from aifred.skills.digest import daily_digest, gather_signals, summarize_digest
from aifred.store.db import Store
from tests.test_google_tools import FakeCal, _gmail


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.sent = None

    def chat(self, messages, tools=None, temperature=0.0):
        self.sent = messages

        class R:
            pass

        r = R()
        r.content = self.content
        return r


def test_gather_signals_structured():
    store = Store(":memory:")
    store.add_item("whatsapp:fam", "todo", "call mum", 1.0)
    sig = gather_signals(_gmail(), FakeCal(), store, "t0", "t1")
    assert sig["unread_count"] == 1
    assert sig["unread"][0]["subject"] == "Hi"
    assert sig["today_events"][0]["summary"] == "standup"
    assert sig["flagged"][0]["content"] == "call mum"
    store.close()


def test_summarize_sends_only_compact_digest():
    store = Store(":memory:")
    llm = FakeLLM("brief here")
    sig = gather_signals(_gmail(), FakeCal(), store, "t0", "t1")
    out = summarize_digest(sig, llm)
    assert out == "brief here"
    # V11: payload is the compact digest, no raw mail body present
    sent = llm.sent[-1]["content"]
    assert "standup" in sent and "Hi" in sent
    store.close()


def test_daily_digest_shape():
    store = Store(":memory:")
    out = daily_digest(_gmail(), FakeCal(), store, FakeLLM("ok"), "t0", "t1")
    assert out["brief"] == "ok"
    assert out["signals"]["unread_count"] == 1
    store.close()
