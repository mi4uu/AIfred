"""Telegram bot (I.telegram). Long-poll via httpx — no extra dependency.

Security: allowed-users gate. Messages from any user not in AIFRED_TELEGRAM_
ALLOWED_USERS are ignored (the bot handles family/private data). Each allowed
message runs through the AgentLoop; the reply is sent back.

httpx.Client injectable so getUpdates/sendMessage can be tested offline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

API = "https://api.telegram.org"


def parse_allowed_users(raw: str) -> set[int]:
    """Parse 'AIFRED_TELEGRAM_ALLOWED_USERS' csv into a set of int ids."""
    out: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


@dataclass
class TelegramBot:
    token: str
    allowed_users: set[int]
    agent: object  # AgentLoop-like with .run(text)->{"reply":...}
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=70.0))
    callback_resolver: object | None = None  # .handle_callback(data:str)->str|None (V27)

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_users

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        body = {"chat_id": chat_id, "text": text}
        if reply_markup:
            body["reply_markup"] = reply_markup
        self.client.post(self._url("sendMessage"), json=body)

    def send_proposal(self, chat_id: int, text: str, pid: int) -> None:
        """Push a confirm prompt with ✅/❌ inline buttons (V27)."""
        kb = {"inline_keyboard": [[
            {"text": "✅ Tak", "callback_data": f"cal_ok:{pid}"},
            {"text": "❌ Nie", "callback_data": f"cal_no:{pid}"},
        ]]}
        self.send_message(chat_id, text, reply_markup=kb)

    def _answer_callback(self, callback_id: str, text: str = "") -> None:
        self.client.post(self._url("answerCallbackQuery"),
                         json={"callback_query_id": callback_id, "text": text[:200]})

    def handle_update(self, update: dict) -> str | None:
        """Process one update. Returns reply text sent, or None if ignored."""
        cb = update.get("callback_query")
        if cb:
            return self._handle_callback(cb)
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return None
        user_id = msg.get("from", {}).get("id")
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "")
        if user_id is None or not self.is_allowed(user_id):  # security gate
            return None
        if not text:
            return None
        result = self.agent.run(text)
        reply = result.get("reply", "") if isinstance(result, dict) else str(result)
        self.send_message(chat_id, reply)
        return reply

    def _handle_callback(self, cb: dict) -> str | None:
        user_id = cb.get("from", {}).get("id")
        if user_id is None or not self.is_allowed(user_id):  # same security gate
            return None
        data = cb.get("data", "")
        reply = self.callback_resolver.handle_callback(data) if self.callback_resolver else None
        self._answer_callback(cb.get("id", ""), reply or "")
        chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
        if reply and chat_id is not None:
            self.send_message(chat_id, reply)
        return reply

    def poll_once(self, offset: int = 0, timeout: int = 50) -> int:
        resp = self.client.get(self._url("getUpdates"), params={"offset": offset, "timeout": timeout})
        resp.raise_for_status()
        data = resp.json()
        new_offset = offset
        for upd in data.get("result", []):
            self.handle_update(upd)
            new_offset = max(new_offset, upd.get("update_id", 0) + 1)
        return new_offset

    def run(self, poll_interval: float = 1.0) -> None:  # pragma: no cover (loop)
        offset = 0
        while True:
            try:
                offset = self.poll_once(offset)
            except httpx.HTTPError:
                time.sleep(poll_interval)
