"""WhatsApp ingest (I.whatsapp, V5, V13).

Transport-agnostic: any source (whatsmeow bridge webhook, baileys sidecar)
normalizes to a common shape, then ingest_batch dedups into the store and
advances the per-chat cursor (V13). SingleInstanceLock enforces exactly one
live connection (V5) — second holder fails loud, mirroring baileys'
`conflict: replaced` but caught on our side.

Recommended transport: whatsmeow bridge POSTing messages to AIfred webhook.
"""

from __future__ import annotations

import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aifred.store.db import Store


class WhatsAppLockError(Exception):
    """Another whatsapp connection already holds the lock (V5)."""


class SingleInstanceLock:
    """File lock — only one whatsapp connection at a time (V5)."""

    def __init__(self, lock_path: str | Path):
        self.path = Path(lock_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None

    def acquire(self) -> None:
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self._fh.close()
            self._fh = None
            raise WhatsAppLockError(f"whatsapp already connected (lock {self.path}) (V5)") from e

    def release(self) -> None:
        if self._fh:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


@dataclass
class WAMessage:
    ext_id: str
    chat_id: str
    sender: str
    ts: float
    body: str
    raw: dict[str, Any]
    sender_name: str = ""  # WhatsApp PushName (display name) — for contact linking
    is_group: bool = False
    from_me: bool = False  # owner's own outgoing message (IsFromMe) — skip in triage (V22)


class WhatsAppSource(Protocol):
    """Adapter any transport implements. fetch_new returns normalized msgs."""

    def fetch_new(self) -> list[WAMessage]: ...


def normalize(raw: dict[str, Any]) -> WAMessage:
    """Map a provider payload to WAMessage. Tolerant of baileys/whatsmeow keys."""
    ext_id = str(raw.get("id") or raw.get("key", {}).get("id") or "")
    chat_id = str(raw.get("chat") or raw.get("chatId") or raw.get("key", {}).get("remoteJid") or "")
    sender = str(raw.get("sender") or raw.get("participant") or raw.get("from") or chat_id)
    ts = float(raw.get("ts") or raw.get("timestamp") or raw.get("messageTimestamp") or 0)
    body = str(
        raw.get("body")
        or raw.get("text")
        or raw.get("message", {}).get("conversation", "")
        or ""
    )
    if not ext_id:
        raise ValueError("whatsapp message missing id — cannot dedup (V13)")
    return WAMessage(ext_id=ext_id, chat_id=chat_id, sender=sender, ts=ts, body=body, raw=raw)


def ingest_batch(store: Store, messages: list[WAMessage], channel: str = "whatsapp") -> int:
    """Idempotent ingest into store; dedup by ext_id (V13). Returns new count."""
    new = 0
    for m in messages:
        if store.add_message(
            channel, m.chat_id, m.ext_id, m.ts, m.body, m.sender, m.raw, m.sender_name, m.is_group, m.from_me
        ):
            new += 1
    return new
