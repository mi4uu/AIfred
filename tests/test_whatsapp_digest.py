"""T10 whatsapp digest tests (V11 shortlist-only, V15 code prefilter)."""

from aifred.store.db import Store
from aifred.whatsapp.digest import prefilter, run_digest, summarize
from aifred.whatsapp.ingest import WAMessage


def m(ext_id, sender, body, ts=1.0):
    return WAMessage(ext_id=ext_id, chat_id="fam", sender=sender, ts=ts, body=body, raw={})


def test_prefilter_keeps_actionable_drops_chatter():
    msgs = [
        m("1", "mum", "haha lol"),               # chatter -> drop
        m("2", "mum", "can you pick up milk today?"),  # kw+question+date -> keep
        m("3", "dad", "meeting tomorrow at 3pm"),      # kw+time+date -> keep
        m("4", "bob", "ok"),                     # drop
    ]
    kept = prefilter(msgs, important_senders={"mum"})
    bodies = [s.msg.body for s in kept]
    assert "can you pick up milk today?" in bodies
    assert "meeting tomorrow at 3pm" in bodies
    assert "haha lol" not in bodies
    assert "ok" not in bodies


def test_prefilter_limit():
    msgs = [m(str(i), "x", "urgent payment today?") for i in range(40)]
    assert len(prefilter(msgs, limit=10)) == 10


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.0):
        self.calls.append(messages)

        class R:
            pass

        r = R()
        r.content = self.content
        return r


def test_summarize_only_sends_shortlist():
    msgs = [m("1", "mum", "can you pay invoice today?"), m("2", "x", "lol")]
    shortlist = prefilter(msgs)
    llm = FakeLLM('{"summary":"pay invoice","items":[{"kind":"todo","content":"pay invoice"}]}')
    out = summarize(shortlist, llm)
    assert out["items"][0]["content"] == "pay invoice"
    # V11: user message contains only shortlisted msgs, not raw "lol"
    sent = llm.calls[0][-1]["content"]
    assert "lol" not in sent and "invoice" in sent


def test_run_digest_persists_items_to_brain_and_store():
    msgs = [m("1", "mum", "doctor appointment tomorrow 10am?")]
    llm = FakeLLM('{"summary":"appt","items":[{"kind":"event","content":"doctor 10am"}]}')

    appended = []

    class FakeBrain:
        def append(self, content):
            appended.append(content)

    store = Store(":memory:")
    out = run_digest(msgs, llm, brain=FakeBrain(), store=store, now_ts=1.0, chat_id="fam")
    assert out["scanned"] == 1 and out["shortlist_size"] == 1
    assert any("doctor 10am" in a for a in appended)  # V2 brain
    assert [r["content"] for r in store.list_items("open")] == ["doctor 10am"]
    store.close()


def test_empty_shortlist_no_llm_call():
    llm = FakeLLM("{}")
    out = run_digest([m("1", "x", "ok")], llm)
    assert out == {"summary": "", "items": [], "shortlist_size": 0, "scanned": 1}
    assert llm.calls == []  # no LLM when nothing actionable (V11)
