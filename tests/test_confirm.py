"""T15 confirm layer tests (V7)."""

import json

import pytest
from pydantic import BaseModel

from aifred.agent.loop import AgentLoop
from aifred.agent.router import IntentRouter
from aifred.confirm import ConfirmManager
from aifred.tools.base import tool_from_model
from aifred.tools.registry import ToolRegistry


def test_deny_blocks_side_effects():
    cm = ConfirmManager(mode="deny")
    assert cm.hook("calendar_create", {}) is False


def test_allow_all():
    cm = ConfirmManager(mode="allow_all")
    assert cm.hook("calendar_create", {}) is True


def test_preauth_specific_tool():
    cm = ConfirmManager(mode="deny")
    cm.pre_authorize("calendar_create")
    assert cm.hook("calendar_create", {}) is True
    assert cm.hook("send_mail", {}) is False  # others still blocked


def test_ask_stages_pending_then_approve():
    cm = ConfirmManager(mode="ask")
    assert cm.hook("calendar_create", {"summary": "x"}) is False  # deferred
    pend = cm.list_pending()
    assert len(pend) == 1 and pend[0].tool == "calendar_create"
    got = cm.approve(pend[0].token)
    assert got.args["summary"] == "x"
    assert cm.list_pending() == []


def test_approve_unknown_raises():
    cm = ConfirmManager(mode="ask")
    with pytest.raises(KeyError):
        cm.approve("nope")


# integration: loop denies, then preauth allows


class EchoArgs(BaseModel):
    text: str


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, temperature=0.0):
        return self.script.pop(0)


class R:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


def _call(name, args):
    return {"id": "c", "function": {"name": name, "arguments": json.dumps(args)}}


def test_loop_uses_confirm_manager():
    r = ToolRegistry()
    r.register(tool_from_model("send_mail", "send", EchoArgs, lambda text: "sent", side_effecting=True, tags=("gmail",)))
    cm = ConfirmManager(mode="deny")
    cm.pre_authorize("send_mail")
    llm = FakeLLM([R(tool_calls=[_call("send_mail", {"text": "hi"})]), R(content="done")])
    loop = AgentLoop(llm=llm, registry=r, router=IntentRouter(r), confirm=cm.hook)
    out = loop.run("send mail")
    assert any(m.get("content") == "sent" for m in out["messages"] if m.get("role") == "tool")
