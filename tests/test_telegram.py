"""T16 telegram tests. MockTransport, no network.

Covers allowed-users gate (security) + agent routing + offset advance.
"""

import json

import httpx

from aifred.telegram.bot import TelegramBot, parse_allowed_users


class FakeAgent:
    def __init__(self):
        self.seen = []

    def run(self, text, history=None):
        self.seen.append(text)
        return {"reply": f"echo:{text}"}


def _bot(handler, agent, allowed):
    return TelegramBot(
        token="T", allowed_users=allowed, agent=agent,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_parse_allowed_users():
    assert parse_allowed_users("111, 222;333 ,x") == {111, 222, 333}


def test_disallowed_user_ignored():
    sent = []

    def handler(req):
        if req.url.path.endswith("sendMessage"):
            sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    agent = FakeAgent()
    bot = _bot(handler, agent, allowed={111})
    upd = {"message": {"from": {"id": 999}, "chat": {"id": 999}, "text": "hi"}}
    assert bot.handle_update(upd) is None  # gate
    assert agent.seen == []  # agent never ran
    assert sent == []  # nothing sent


def test_callback_query_routes_to_resolver_and_gated():
    sent, answered = [], []

    def handler(req):
        if req.url.path.endswith("sendMessage"):
            sent.append(json.loads(req.content))
        if req.url.path.endswith("answerCallbackQuery"):
            answered.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    class Resolver:
        def handle_callback(self, data):
            return f"done:{data}"

    bot = _bot(handler, FakeAgent(), allowed={111})
    bot.callback_resolver = Resolver()

    # allowed user taps a button -> resolver runs, answerCallbackQuery + message sent
    cb = {"callback_query": {"id": "c1", "from": {"id": 111}, "data": "cal_ok:5",
                             "message": {"chat": {"id": 7}}}}
    assert bot.handle_update(cb) == "done:cal_ok:5"
    assert answered and any(m.get("text") == "cal_ok:5" or "done" in str(m) for m in answered)
    assert any(m["chat_id"] == 7 for m in sent)

    # disallowed user tapping a button is ignored (security gate)
    sent.clear(); answered.clear()
    cb2 = {"callback_query": {"id": "c2", "from": {"id": 999}, "data": "cal_ok:5",
                              "message": {"chat": {"id": 7}}}}
    assert bot.handle_update(cb2) is None
    assert sent == [] and answered == []


def test_send_proposal_inline_keyboard():
    sent = []

    def handler(req):
        if req.url.path.endswith("sendMessage"):
            sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    bot = _bot(handler, FakeAgent(), allowed={111})
    bot.send_proposal(7, "🗓️ event?", 42)
    assert sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "cal_ok:42"
    assert sent[0]["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == "cal_no:42"


def test_allowed_user_routed_and_replied():
    sent = []

    def handler(req):
        if req.url.path.endswith("sendMessage"):
            sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    agent = FakeAgent()
    bot = _bot(handler, agent, allowed={111})
    upd = {"message": {"from": {"id": 111}, "chat": {"id": 5}, "text": "status?"}}
    reply = bot.handle_update(upd)
    assert reply == "echo:status?"
    assert agent.seen == ["status?"]
    assert sent[0] == {"chat_id": 5, "text": "echo:status?"}


def test_poll_once_advances_offset():
    def handler(req):
        if req.url.path.endswith("getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 10, "message": {"from": {"id": 111}, "chat": {"id": 5}, "text": "a"}},
                {"update_id": 11, "message": {"from": {"id": 111}, "chat": {"id": 5}, "text": "b"}},
            ]})
        return httpx.Response(200, json={"ok": True})

    bot = _bot(handler, FakeAgent(), allowed={111})
    assert bot.poll_once(0) == 12  # max update_id + 1
