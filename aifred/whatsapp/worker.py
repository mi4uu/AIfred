"""WhatsApp worker (I.whatsapp, V5, V13) — neonize / whatsmeow.

whatsmeow (via neonize) is the reliable transport: multi-device, auto-reconnect,
history sync, persistent session. Far steadier than baileys/whatscli.

First-time pairing (QR) must be done once with a terminal:
    uv run python -m aifred.whatsapp.worker
Scan the QR with WhatsApp > Linked devices. The session persists in the sqlite
session file, so the systemd service reconnects silently afterwards.

Runtime: every incoming message is normalized and ingested into the store
(dedup by id, V13). The single-instance lock (V5) prevents a second connection
from fighting the first.

neonize is imported lazily so this module (and the test suite) load without it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

from aifred.store.db import Store
from aifred.whatsapp.ingest import SingleInstanceLock, WAMessage, ingest_batch

log = logging.getLogger("aifred.whatsapp")


def _getattr_path(obj, *names, default=None):
    for n in names:
        obj = getattr(obj, n, None)
        if obj is None:
            return default
    return obj


def derive_is_group(chat: str, sender: str) -> bool:
    """Robust group detection from JIDs alone (V18).

    The old len>=15 heuristic flapped and the history-sync path never set the
    flag at all, so family-group messages addressed to someone else leaked
    through as 'high' (the Anna-on-Telegram bug, B3). Signals, any one is
    decisive:
      - JID suffix @g.us / @broadcast / @newsletter
      - WhatsApp group/newsletter numeric prefix 120363
      - participant (sender) differs from the chat JID  -> it's a room
      - very long (>=16 digit) chat JID
    Decide from the CHAT JID only — never from sender. WhatsApp now hands out
    @lid sender IDs that differ from the phone-chat JID even in direct chats, so
    'sender != chat' falsely flags 1:1 conversations as groups.
    Direct chats keep short numeric chat JIDs (a phone number, <=14 digits).
    """
    c = str(chat or "")
    if c == "status" or any(c.endswith(x) for x in ("g.us", "broadcast", "newsletter")):
        return True
    cd = re.sub(r"\D", "", c)
    if cd.startswith("120363"):
        return True
    return len(cd) >= 15  # group JIDs run 15-18 digits; real phone numbers <=14


def normalize_neonize(message) -> WAMessage | None:
    """Map a neonize MessageEv to WAMessage. Tolerant of proto shape."""
    info = getattr(message, "Info", None)
    if info is None:
        return None
    src = getattr(info, "MessageSource", info)
    chat = _getattr_path(src, "Chat", "User") or str(_getattr_path(src, "Chat", default=""))
    sender = _getattr_path(src, "Sender", "User") or str(_getattr_path(src, "Sender", default=""))
    ext_id = getattr(info, "ID", "") or ""
    ts_obj = getattr(info, "Timestamp", 0)
    ts = float(getattr(ts_obj, "seconds", ts_obj) or 0)
    push_name = getattr(info, "Pushname", "") or getattr(info, "PushName", "") or ""
    is_group = bool(getattr(src, "IsGroup", False)) or derive_is_group(str(chat), str(sender))
    from_me = bool(getattr(src, "IsFromMe", False) or getattr(info, "IsFromMe", False))
    msg = getattr(message, "Message", None)
    body = ""
    if msg is not None:
        body = getattr(msg, "conversation", "") or _getattr_path(msg, "extendedTextMessage", "text", default="") or ""
        if not body:  # image/media — mark it (non-empty) so it's ingested + visible (V36)
            cap = _getattr_path(msg, "imageMessage", "caption", default="")
            if getattr(msg, "imageMessage", None) is not None and getattr(msg.imageMessage, "URL", None) is not None:
                body = f"[obraz] {cap}".strip()
    if not ext_id:
        return None
    return WAMessage(
        ext_id=str(ext_id), chat_id=str(chat), sender=str(sender), ts=ts,
        body=str(body), raw={}, sender_name=str(push_name), is_group=is_group, from_me=from_me,
    )


def _msg_from_webinfo(info) -> WAMessage | None:
    """Extract a WAMessage from a WebMessageInfo proto (history sync)."""
    key = getattr(info, "key", None) or getattr(info, "Key", None)
    if key is None:
        return None
    ext_id = getattr(key, "ID", "") or getattr(key, "id", "")
    chat = getattr(key, "remoteJID", "") or getattr(key, "remoteJid", "") or getattr(key, "RemoteJID", "")
    sender = getattr(key, "participant", "") or getattr(key, "Participant", "") or chat
    from_me = bool(getattr(key, "fromMe", False) or getattr(key, "FromMe", False))
    ts = float(getattr(info, "messageTimestamp", 0) or getattr(info, "MessageTimestamp", 0) or 0)
    inner = getattr(info, "message", None) or getattr(info, "Message", None)
    body = ""
    if inner is not None:
        body = getattr(inner, "conversation", "") or _getattr_path(inner, "extendedTextMessage", "text", default="") or ""
    if not ext_id:
        return None
    return WAMessage(
        ext_id=str(ext_id), chat_id=str(chat), sender=str(sender), ts=ts, body=str(body), raw={},
        is_group=derive_is_group(str(chat), str(sender)),  # B3: backfill path lost the group flag
        from_me=from_me,
    )


def ingest_history(store: Store, ev) -> int:
    """Walk a HistorySyncEv and ingest its messages (backfill). Best-effort, tolerant."""
    data = getattr(ev, "Data", None) or getattr(ev, "data", None) or ev
    conversations = getattr(data, "conversations", None) or getattr(data, "Conversations", None) or []
    batch: list[WAMessage] = []
    for conv in conversations:
        for hmsg in getattr(conv, "messages", None) or getattr(conv, "Messages", None) or []:
            info = getattr(hmsg, "message", None) or getattr(hmsg, "Message", None) or hmsg
            wam = _msg_from_webinfo(info)
            if wam is not None:
                batch.append(wam)
    return ingest_batch(store, batch)


def run_whatsapp_worker(
    store: Store,
    session_path: str,
    lock_path: str,
    on_message: Callable[[WAMessage], None] | None = None,
) -> None:  # pragma: no cover (needs live whatsmeow + paired device)
    """Connect and stream messages into the store. Blocks. Hold lock for V5."""
    from neonize.client import NewClient
    from neonize.events import ConnectedEv, MessageEv

    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    lock = SingleInstanceLock(lock_path)
    lock.acquire()  # V5: fail loud if another connection holds it
    log.info("whatsapp worker starting; session=%s", session_path)

    client = NewClient(session_path)

    @client.event(ConnectedEv)
    def _on_connect(_c, _e):  # noqa: ANN001
        log.info("whatsapp connected")

    @client.event(MessageEv)
    def _on_message(_c, message):  # noqa: ANN001
        wam = normalize_neonize(message)
        if wam is None:
            return
        ingest_batch(store, [wam])  # V13 dedup into store
        if on_message:
            on_message(wam)

    try:
        client.connect()  # blocks; prints QR to terminal if unpaired
    finally:
        lock.release()


def _main() -> None:  # pragma: no cover
    import logging as _l

    from aifred.config import get_settings

    _l.basicConfig(level=_l.INFO)
    s = get_settings()
    session = s.whatsapp_session_path or f"{s.data_dir}/wa.sqlite"
    store = Store(f"{s.data_dir}/aifred.db")
    run_whatsapp_worker(store, session, f"{s.data_dir}/wa.lock")


if __name__ == "__main__":
    _main()
