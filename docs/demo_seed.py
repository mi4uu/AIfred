"""Seeds a throwaway Store with placeholder data and serves the web UI + API
against it, so you can see AIfred's shape without a real brain.md, Google,
Telegram, or WhatsApp account. No real accounts, no tokens.

Run `cd web && bun install && bun run build && cd ..` first, then:
    uv run python docs/demo_seed.py
and open http://127.0.0.1:9120
"""

from __future__ import annotations

import tempfile
import time

from aifred.confirm import ConfirmManager
from aifred.main import create_app
from aifred.store.db import Store


class FakeAgent:
    def run(self, message: str, history: list | None = None, on_step=None) -> dict:
        return {"reply": "Jasne, sprawdzam.", "pending": []}


def seed(store: Store) -> None:
    now = time.time()
    sid = store.create_session(ts=now, title="Zakupy na weekend")
    store.add_chat_message(sid, "user", "Przypomnij mi co mam kupić na weekend", now)
    store.add_chat_message(
        sid, "assistant",
        "Z Twoich notatek: mleko, jajka, kawa. Dodałem to też do zadań.",
        now + 1,
    )
    store.create_session(ts=now - 100, title="Spotkanie w czwartek")
    store.create_session(ts=now - 200, title="Mail od dostawcy")

    store.add_attention(
        "triage:whatsapp", "high", "[whatsapp] Jan: jutro o 10 masz to spotkanie, pamiętasz?",
        "demo:wa:1", now,
    )
    store.add_attention(
        "triage:gmail", "medium", "[gmail] Faktura za listopad jest już dostępna",
        "demo:mail:1", now,
    )

    store.add_rule("sender", "newsletter@example.com", "mute", ts=now)


def main() -> None:
    import uvicorn

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(f"{tmp}/demo.db")
        seed(store)
        app = create_app(agent=FakeAgent(), confirm=ConfirmManager(), store=store)
        uvicorn.run(app, host="127.0.0.1", port=9120)


if __name__ == "__main__":
    main()
