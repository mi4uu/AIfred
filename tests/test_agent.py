"""T4 agent loop + router tests. Fake LLM, no network.

Covers V14 router scoping, tool dispatch, V7 confirm gate, V9 fail-loud.
"""

import json
from datetime import datetime

from pydantic import BaseModel

from aifred.agent.loop import AgentLoop, _now_hint
from aifred.agent.router import IntentRouter
from aifred.tools.base import tool_from_model
from aifred.tools.registry import ToolRegistry
from aifred.tz import get_timezone


class EchoArgs(BaseModel):
    text: str


def _build_registry():
    r = ToolRegistry()
    r.register(tool_from_model("mail_search", "search mail", EchoArgs, lambda text: f"mail:{text}", tags=("gmail",)))
    r.register(tool_from_model("cal_list", "list events", EchoArgs, lambda text: f"cal:{text}", tags=("calendar",)))
    r.register(tool_from_model("send_mail", "send mail", EchoArgs, lambda text: "sent", side_effecting=True, tags=("gmail",)))
    return r


def test_router_scopes_by_keyword():
    r = _build_registry()
    router = IntentRouter(r)
    assert set(router.select("check my email")) == {"mail_search", "send_mail"}
    assert set(router.select("what events today")) == {"cal_list"}
    assert router.select("hello there") == []  # only core (none tagged core here)


class FakeLLM:
    """Scripted responses: first a tool call, then a final answer."""

    def __init__(self, script):
        self.script = list(script)
        self.seen_tools = []

    def chat(self, messages, tools=None, temperature=0.0):
        self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
        return self.script.pop(0)


class R:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


def _tool_call(name, args):
    return {"id": "c1", "function": {"name": name, "arguments": json.dumps(args)}}


def test_loop_dispatches_tool_then_answers():
    r = _build_registry()
    llm = FakeLLM([
        R(tool_calls=[_tool_call("mail_search", {"text": "hi"})]),
        R(content="found 1 mail"),
    ])
    loop = AgentLoop(llm=llm, registry=r, router=IntentRouter(r))
    out = loop.run("check email")
    assert out["reply"] == "found 1 mail"
    # V14: only gmail-tagged tools were offered
    assert set(llm.seen_tools[0]) == {"mail_search", "send_mail"}
    # tool result present in transcript
    assert any(m.get("role") == "tool" and m["content"] == "mail:hi" for m in out["messages"])


def test_side_effecting_denied_without_confirm():
    r = _build_registry()
    llm = FakeLLM([
        R(tool_calls=[_tool_call("send_mail", {"text": "yo"})]),
        R(content="ok"),
    ])
    loop = AgentLoop(llm=llm, registry=r, router=IntentRouter(r))  # default deny (V7)
    out = loop.run("send mail")
    assert any("DENIED" in m.get("content", "") for m in out["messages"] if m.get("role") == "tool")


def test_now_hint_states_date_and_zone_but_not_an_offset_to_reuse(monkeypatch):
    winter = datetime(2026, 11, 3, 9, 30, tzinfo=get_timezone("Europe/Warsaw"))
    monkeypatch.setattr("aifred.tz.datetime", type("D", (), {"now": staticmethod(lambda tz: winter)}))
    hint = _now_hint("Europe/Warsaw")
    assert "2026-11-03" in hint
    assert "Europe/Warsaw" in hint
    # must not hand the model "today's" offset to stamp on every event: a
    # calendar event on the other side of a DST change would get the wrong
    # one (V35 follow-up). Offsets are attached in code, per event date.
    assert "+01:00" not in hint and "+02:00" not in hint


def test_now_hint_same_shape_across_dst(monkeypatch):
    summer = datetime(2026, 7, 3, 9, 30, tzinfo=get_timezone("Europe/Warsaw"))
    monkeypatch.setattr("aifred.tz.datetime", type("D", (), {"now": staticmethod(lambda tz: summer)}))
    hint = _now_hint("Europe/Warsaw")
    assert "2026-07-03" in hint
    assert "Europe/Warsaw" in hint


def test_side_effecting_allowed_with_confirm():
    r = _build_registry()
    llm = FakeLLM([
        R(tool_calls=[_tool_call("send_mail", {"text": "yo"})]),
        R(content="done"),
    ])
    loop = AgentLoop(llm=llm, registry=r, router=IntentRouter(r), confirm=lambda n, a: True)
    out = loop.run("send mail")
    assert any(m.get("content") == "sent" for m in out["messages"] if m.get("role") == "tool")
