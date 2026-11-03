"""T17 web API tests (I.web auth, chat routing, confirm flow, SSE, sessions)."""

import json

from fastapi.testclient import TestClient

from aifred.confirm import ConfirmManager
from aifred.main import create_app
from aifred.store.db import Store


class FakeAgent:
    def run(self, message, history=None, on_step=None):
        if on_step:
            on_step("thinking")
            on_step("tool", "brain_context")
        return {"reply": f"echo:{message}", "history_len": len(history or [])}


def test_chat_routes_to_agent():
    app = create_app(agent=FakeAgent(), confirm=ConfirmManager())
    c = TestClient(app)
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == "echo:hi"


def test_auth_required_when_token_set(monkeypatch):
    monkeypatch.setenv("AIFRED_WEB_TOKEN", "secret")
    from aifred.config import get_settings

    get_settings.cache_clear()
    app = create_app(agent=FakeAgent(), confirm=ConfirmManager())
    c = TestClient(app)
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 401
    ok = c.post("/api/chat", json={"message": "hi"}, headers={"X-AIfred-Token": "secret"})
    assert ok.status_code == 200
    # same length, wrong value: constant-time compare still rejects it
    wrong = c.post("/api/chat", json={"message": "hi"}, headers={"X-AIfred-Token": "xxxxxx"})
    assert wrong.status_code == 401
    # malformed (non-ASCII) header must reject with 401, not crash with 500.
    # Raw bytes bypass the test client's own ascii-only str header encoding,
    # the same way a hostile or just-broken client's header would arrive.
    malformed = c.post("/api/chat", json={"message": "hi"},
                        headers={"X-AIfred-Token": b"\xe9\xe9\xe9\xe9\xe9\xe9"})
    assert malformed.status_code == 401
    get_settings.cache_clear()


class FakeRegistry:
    """Records dispatch calls, like ToolRegistry but no real tools needed."""

    def __init__(self):
        self.calls = []

    def dispatch(self, name, args):
        self.calls.append((name, args))
        return {"ok": True, "echo": args}


class FakeAgentWithRegistry(FakeAgent):
    def __init__(self):
        self.registry = FakeRegistry()


def test_confirm_approve_executes_the_exact_pending_args_once():
    cm = ConfirmManager(mode="ask")
    cm.hook("calendar_create", {"summary": "x", "start": "2026-06-09T16:40:00+02:00"})
    token = cm.list_pending()[0].token
    fake_agent = FakeAgentWithRegistry()
    app = create_app(agent=fake_agent, confirm=cm)
    c = TestClient(app)
    r = c.post("/api/confirm", json={"token": token, "approve": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["result"] == {"ok": True, "echo": {"summary": "x", "start": "2026-06-09T16:40:00+02:00"}}
    # executed the exact approved args, exactly once
    assert fake_agent.registry.calls == [("calendar_create", {"summary": "x", "start": "2026-06-09T16:40:00+02:00"})]
    # a single approval does NOT blanket-preauthorize the tool for future calls
    assert "calendar_create" not in cm.preauth


def test_confirm_approve_with_always_also_preauthorizes():
    cm = ConfirmManager(mode="ask")
    cm.hook("calendar_create", {"summary": "x"})
    token = cm.list_pending()[0].token
    fake_agent = FakeAgentWithRegistry()
    app = create_app(agent=fake_agent, confirm=cm)
    c = TestClient(app)
    r = c.post("/api/confirm", json={"token": token, "approve": True, "always": True})
    assert r.status_code == 200
    assert "calendar_create" in cm.preauth  # explicit opt-in, not the default


def test_confirm_reject_does_not_dispatch():
    cm = ConfirmManager(mode="ask")
    cm.hook("calendar_create", {"summary": "x"})
    token = cm.list_pending()[0].token
    fake_agent = FakeAgentWithRegistry()
    app = create_app(agent=fake_agent, confirm=cm)
    c = TestClient(app)
    r = c.post("/api/confirm", json={"token": token, "approve": False})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert fake_agent.registry.calls == []
    assert "calendar_create" not in cm.preauth


def test_no_api_when_no_agent():
    app = create_app()  # no agent -> no chat route (static UI may catch the path)
    c = TestClient(app)
    assert c.post("/api/chat", json={"message": "hi"}).status_code in (404, 405)
    assert c.get("/health").status_code == 200


class HistoryRecordingAgent(FakeAgent):
    """Like FakeAgent, but remembers the `history` it was called with each
    turn, so a test can assert on the exact prior conversation it received."""

    def __init__(self):
        self.history_calls = []

    def run(self, message, history=None, on_step=None):
        self.history_calls.append(history)
        return super().run(message, history=history, on_step=on_step)


def _client_with_store(tmp_path, agent=None):
    store = Store(str(tmp_path / "s.db"))
    agent = agent or FakeAgent()
    app = create_app(agent=agent, confirm=ConfirmManager(), store=store)
    return TestClient(app), store, agent


def test_sessions_crud(tmp_path):
    c, store, _agent = _client_with_store(tmp_path)
    assert c.get("/api/sessions").json()["sessions"] == []
    sid = c.post("/api/sessions").json()["id"]
    assert len(c.get("/api/sessions").json()["sessions"]) == 1
    c.patch(f"/api/sessions/{sid}", json={"title": "Groceries"})
    assert c.get("/api/sessions").json()["sessions"][0]["title"] == "Groceries"
    c.delete(f"/api/sessions/{sid}")
    assert c.get("/api/sessions").json()["sessions"] == []
    store.close()


def test_chat_stream_sse_events_and_persist(tmp_path):
    c, store, _agent = _client_with_store(tmp_path)
    r = c.post("/api/chat/stream", json={"message": "kim jesteś?"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    events = [json.loads(line[len("data: "):]) for line in r.text.splitlines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert "session" in types and "status" in types and "reply" in types
    # status carried the tool name (UI indicator)
    assert any(e.get("event") == "tool" and e.get("detail") == "brain_context" for e in events)
    reply = next(e for e in events if e["type"] == "reply")
    assert reply["reply"] == "echo:kim jesteś?"
    sid = reply["session_id"]
    # persisted: user + assistant turns, session auto-titled from first msg
    msgs = c.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert c.get("/api/sessions").json()["sessions"][0]["title"].startswith("kim jeste")
    store.close()


def test_review_queue_decision_creates_rule(tmp_path):
    c, store, _agent = _client_with_store(tmp_path)
    meta = json.dumps({"source": "whatsapp", "sender": "48500", "person": "Anna",
                       "domain": "", "is_group": False, "suggest": "high"})
    store.add_attention("triage:whatsapp", "medium", "[whatsapp] Anna: hej", "wa:1", 1.0,
                        status="review", meta=meta)
    listed = c.get("/api/review").json()["items"]
    assert len(listed) == 1 and listed[0]["suggest"] == "high"
    item_id = listed[0]["id"]
    # owner says "important" -> vip rule on the person, item promoted to high+open
    r = c.post(f"/api/review/{item_id}", json={"decision": "important"})
    assert r.status_code == 200 and r.json()["action"] == "vip"
    assert any(rl["pattern"] == "anna" and rl["action"] == "vip" for rl in store.list_rules())
    assert c.get("/api/review").json()["items"] == []  # left the queue
    feed = c.get("/api/attention").json()["items"]
    assert any(i["importance"] == "high" for i in feed)  # now in the feed
    store.close()


def test_review_mute_decision(tmp_path):
    c, store, _agent = _client_with_store(tmp_path)
    meta = json.dumps({"source": "mail", "sender": "promo@x.com", "person": "",
                       "domain": "x.com", "is_group": False, "suggest": "high"})
    store.add_attention("triage:mail", "medium", "[mail] promo", "m:1", 1.0, status="review", meta=meta)
    item_id = c.get("/api/review").json()["items"][0]["id"]
    r = c.post(f"/api/review/{item_id}", json={"decision": "mute"})
    assert r.json()["scope"] == "domain" and r.json()["action"] == "mute"
    assert any(rl["pattern"] == "x.com" and rl["action"] == "mute" for rl in store.list_rules())
    store.close()


def test_chat_stream_continues_session(tmp_path):
    agent = HistoryRecordingAgent()
    c, store, _agent = _client_with_store(tmp_path, agent=agent)
    sid = c.post("/api/chat/stream", json={"message": "first"}).text
    sid = json.loads(next(ln[6:] for ln in sid.splitlines() if ln.startswith("data: ")))["session_id"]
    r2 = c.post("/api/chat/stream", json={"message": "second", "session_id": sid})
    events = [json.loads(ln[6:]) for ln in r2.text.splitlines() if ln.startswith("data: ")]
    reply = next(e for e in events if e["type"] == "reply")
    assert reply["session_id"] == sid  # same session
    # the agent must actually receive the exact prior turn as history, not
    # just a count: first call gets none, second gets the first exchange back
    assert agent.history_calls[0] == []
    assert [(m["role"], m["content"]) for m in agent.history_calls[1]] == [
        ("user", "first"), ("assistant", "echo:first"),
    ]
    msgs = c.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert len(msgs) == 4  # 2 user + 2 assistant
    store.close()
