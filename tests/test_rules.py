"""Triage rules — learning/correction overrides + owner WhatsApp command."""

import pytest

from aifred.skills.rules import triage_rule
from aifred.store.db import Store
from aifred.tools.base import ToolError
from aifred.triage import TriageEngine, TriageItem


def test_add_and_apply_mute_rule():
    s = Store(":memory:")
    triage_rule(s, "mute", "sender", "Anna")
    eng = TriageEngine(s, llm=None, owner_aliases="Owner")
    items = [TriageItem("whatsapp", "1", "111", "Anna", 1, "", "Do puszek?", importance="high", directed_at_me=True)]
    eng.apply_rules(items)
    assert items[0].importance == "low" and items[0].directed_at_me is False  # muted


def test_vip_rule_forces_high():
    s = Store(":memory:")
    triage_rule(s, "vip", "sender", "Kasia")
    eng = TriageEngine(s, llm=None)
    items = [TriageItem("whatsapp", "1", "111", "Kasia", 1, "", "hej", importance="low")]
    eng.apply_rules(items)
    assert items[0].importance == "high"


def test_domain_and_category_rules():
    s = Store(":memory:")
    triage_rule(s, "mute", "domain", "account.netflix.com")
    triage_rule(s, "low", "category", "kod logowania")
    eng = TriageEngine(s, llm=None)
    items = [
        TriageItem("mail", "1", "info@account.netflix.com", None, 1, "kod logowania", "code", importance="high"),
    ]
    eng.apply_rules(items)
    assert items[0].importance == "low"


def test_rule_validation():
    s = Store(":memory:")
    with pytest.raises(ToolError):
        triage_rule(s, "bogus", "sender", "x")
    with pytest.raises(ToolError):
        triage_rule(s, "mute", "bogus", "x")
    with pytest.raises(ToolError):
        triage_rule(s, "mute", "sender", "")


def test_rule_upsert_and_delete():
    s = Store(":memory:")
    triage_rule(s, "mute", "sender", "Anna")
    triage_rule(s, "vip", "sender", "Anna")  # upsert same scope+pattern
    rules = s.list_rules()
    assert len(rules) == 1 and rules[0]["action"] == "vip"
    s.delete_rule(rules[0]["id"])
    assert s.list_rules() == []


# ---- owner whatsapp command ----

class FakeAgent:
    def __init__(self):
        self.ran = None

    def run(self, msg, history=None, on_step=None):
        self.ran = msg
        return {"reply": f"zrobione: {msg}"}


def test_owner_command_dispatch(monkeypatch):
    from aifred.whatsapp.pairing import WhatsAppManager
    from aifred.whatsapp.ingest import WAMessage

    s = Store(":memory:")
    agent = FakeAgent()
    m = WhatsAppManager(store=s, session_path=":memory:", lock_path="/tmp/x.lock", agent=agent, owner_tail="512345678")

    from types import SimpleNamespace

    sent = []

    class Client:
        def send_message(self, jid, text):
            sent.append((jid, text))

    msg = SimpleNamespace(Info=SimpleNamespace(MessageSource=SimpleNamespace(Chat="selfchat")))
    # from owner (48512345678 -> tail 512345678), mentions Alfred
    wam = WAMessage("1", "48512345678", "48512345678", 1.0, "Alfredzie dodaj zadanie kup mleko", {})
    m._maybe_owner_command(Client(), msg, wam)
    assert agent.ran == "dodaj zadanie kup mleko"  # trigger stripped
    assert sent and "zrobione" in sent[0][1]
    s.close()


def test_non_owner_ignored():
    from aifred.whatsapp.pairing import WhatsAppManager
    from aifred.whatsapp.ingest import WAMessage

    s = Store(":memory:")
    agent = FakeAgent()
    m = WhatsAppManager(store=s, session_path=":memory:", lock_path="/tmp/x.lock", agent=agent, owner_tail="512345678")
    wam = WAMessage("1", "48999999999", "48999999999", 1.0, "Alfredzie zrób coś", {})

    class C:
        def send_message(self, *a):
            raise AssertionError("should not send")

    m._maybe_owner_command(C(), object(), wam)
    assert agent.ran is None  # not owner -> ignored
    s.close()
