"""WhatsApp pairing manager (I.whatsapp, V5) — drives neonize from the web UI.

Holds connection state + the current QR (rendered to a PNG data URL via segno
so the frontend just shows an <img>, no JS QR lib). start() runs neonize in a
background thread under the single-instance lock (V5); incoming messages are
ingested into the store (V13).

neonize imported lazily so this module loads without it (tests, CI).
"""

from __future__ import annotations

import base64
import io
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# owner trigger: "Alfredzie/AIfred/aifredzie ..." (alfred/aifred + Polish endings)
OWNER_TRIGGER = re.compile(r"\ba[il]fred\w*\b", re.I)

from aifred.store.db import Store
from aifred.whatsapp.ingest import SingleInstanceLock, WhatsAppLockError, ingest_batch
from aifred.whatsapp.worker import ingest_history, normalize_neonize

log = logging.getLogger("aifred.whatsapp")


def qr_to_data_url(qr_text: str) -> str:
    """Render a QR string to a PNG data URL (segno). Frontend shows it directly."""
    import segno

    buf = io.BytesIO()
    segno.make(qr_text).save(buf, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@dataclass
class WhatsAppManager:
    store: Store
    session_path: str
    lock_path: str
    on_message: Callable | None = None
    agent: object | None = None       # AgentLoop — for owner commands
    owner_tail: str = ""              # owner's WA number (last 9 digits) — commands only from owner
    media_dir: str = ""               # where downloaded images land (V36)
    state: str = "idle"  # idle | pairing | connected | error
    qr_data_url: str | None = None
    error: str | None = None
    _thread: threading.Thread | None = field(default=None, repr=False)
    _lock: SingleInstanceLock | None = field(default=None, repr=False)

    def image_path(self, ext_id: str) -> str:
        """Deterministic path for a downloaded WhatsApp image (V36)."""
        from pathlib import Path
        base = self.media_dir or "data/media"
        return str(Path(base) / f"{ext_id}.jpg")

    def _save_image(self, client, message, ext_id) -> None:  # pragma: no cover (live)
        """Download an incoming image so vision can read it on demand later."""
        try:
            from pathlib import Path
            data = client.download_any(getattr(message, "Message", None))
            if not data:
                return
            p = Path(self.image_path(ext_id))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            log.info("saved image %s (%d bytes)", ext_id, len(data))
        except Exception as e:  # noqa: BLE001 — media download best-effort
            log.warning("image download failed: %s", e)

    def _resolve_lid(self, client, message) -> None:  # pragma: no cover (live)
        """Map this message's sender @lid -> real phone (V34), so Google/contacts
        can match it. WhatsApp accounts ARE phones; neonize exposes the mapping."""
        try:
            sj = getattr(getattr(getattr(message, "Info", None), "MessageSource", None), "Sender", None)
            if sj is None:
                return
            lid = getattr(sj, "User", "")
            if not lid or getattr(sj, "Server", "") not in ("lid", "hosted.lid", "hosted"):
                return  # already a phone JID, or no user
            if self.store.get_lid_phone(lid):
                return  # cached
            pj = client.get_pn_from_lid(sj)
            phone = re.sub(r"\D", "", getattr(pj, "User", ""))
            if phone:
                self.store.set_lid_phone(lid, phone)
                log.info("lid->phone resolved: %s -> %s", lid, phone)
        except Exception:  # noqa: BLE001 — resolution is best-effort
            pass

    def _backfill_lid_phones(self, client) -> None:  # pragma: no cover (live)
        """On connect, resolve phones for lids already in the store (existing chats)."""
        from neonize.utils.jid import build_jid

        try:
            seen = {r["sender"] for r in self.store.known_sender_names("whatsapp")}
            done = set(self.store.lid_phone_map())
            for lid in seen - done:
                if not lid.isdigit():
                    continue
                try:
                    pj = client.get_pn_from_lid(build_jid(lid, "lid"))
                    phone = re.sub(r"\D", "", getattr(pj, "User", ""))
                    if phone:
                        self.store.set_lid_phone(lid, phone)
                except Exception:  # noqa: BLE001
                    continue
            n = len(self.store.lid_phone_map())
            if n:
                log.info("lid->phone backfill: %d mapped", n)
        except Exception as e:  # noqa: BLE001
            log.warning("lid backfill failed: %s", e)

    def _maybe_owner_command(self, client, message, wam) -> None:  # pragma: no cover (live)
        """If the OWNER messages mentioning Alfred/AIfred, treat as a command."""
        if not (self.agent and self.owner_tail):
            return
        tail = re.sub(r"\D", "", wam.sender)[-9:]
        if tail != self.owner_tail or not OWNER_TRIGGER.search(wam.body or ""):
            return
        cmd = OWNER_TRIGGER.sub("", wam.body).strip(" ,.:!?")
        if not cmd:
            return
        log.info("owner whatsapp command: %s", cmd[:80])
        try:
            res = self.agent.run(cmd)
            reply = res.get("reply", "") if isinstance(res, dict) else str(res)
        except Exception as e:  # noqa: BLE001
            reply = f"błąd: {e}"
        try:
            chat = getattr(getattr(message, "Info", None), "MessageSource", None)
            chat_jid = getattr(chat, "Chat", None)
            if chat_jid is not None and reply:
                client.send_message(chat_jid, reply)
        except Exception as e:  # noqa: BLE001
            log.warning("whatsapp reply failed: %s", e)

    def status(self) -> dict:
        return {
            "state": self.state,
            "qr": self.qr_data_url,
            "error": self.error,
            "paired": Path(self.session_path).exists(),
        }

    def start(self) -> dict:
        """Begin pairing/connection in a background thread. Idempotent."""
        if self._thread and self._thread.is_alive():
            return self.status()
        self.state = "pairing"
        self.qr_data_url = None
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="whatsapp-pair")
        self._thread.start()
        return self.status()

    def _run(self) -> None:  # pragma: no cover (needs live whatsmeow)
        from neonize.client import NewClient
        from neonize.events import ConnectedEv, HistorySyncEv, MessageEv

        try:
            Path(self.session_path).parent.mkdir(parents=True, exist_ok=True)
            self._lock = SingleInstanceLock(self.lock_path)
            self._lock.acquire()  # V5
        except WhatsAppLockError as e:
            self.state = "error"
            self.error = str(e)
            return

        try:
            # show up as "AIfred" in WhatsApp > Linked devices (cosmetic; needs re-pair)
            try:
                from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps

                client = NewClient(self.session_path, props=DeviceProps(os="AIfred", platformType=DeviceProps.CHROME))
            except Exception:  # noqa: BLE001 — fall back to default props
                client = NewClient(self.session_path)

            def _on_qr(_c, qr_bytes):
                self.state = "pairing"
                self.qr_data_url = qr_to_data_url(
                    qr_bytes.decode() if isinstance(qr_bytes, (bytes, bytearray)) else str(qr_bytes)
                )
                log.info("whatsapp QR ready for scan")

            client.event.qr(_on_qr)

            @client.event(ConnectedEv)
            def _on_connect(_c, _e):  # noqa: ANN001
                self.state = "connected"
                self.qr_data_url = None
                log.info("whatsapp connected")
                self._backfill_lid_phones(_c)  # V34: resolve existing lids -> phones

            @client.event(MessageEv)
            def _on_message(_c, message):  # noqa: ANN001
                wam = normalize_neonize(message)
                if wam is None:
                    return
                ingest_batch(self.store, [wam])  # V13
                self._resolve_lid(_c, message)  # V34: map this sender's lid -> phone
                if wam.body.startswith("[obraz]"):
                    self._save_image(_c, message, wam.ext_id)  # V36: download for on-demand vision
                if self.on_message:
                    self.on_message(wam)
                self._maybe_owner_command(_c, message, wam)

            @client.event(HistorySyncEv)
            def _on_history(_c, ev):  # noqa: ANN001 — backfill on pair/initial sync
                try:
                    n = ingest_history(self.store, ev)
                    if n:
                        log.info("whatsapp history backfill: %d messages", n)
                except Exception as e:  # noqa: BLE001
                    log.warning("history sync ingest failed: %s", e)

            client.connect()  # blocks until disconnect / QR timeout
            if self.state != "connected":
                self.state = "idle"  # QR expired unscanned -> let user re-enable
                self.qr_data_url = None
        except Exception as e:  # noqa: BLE001
            self.state = "error"
            self.error = str(e)
            log.error("whatsapp pairing failed: %s", e)
        finally:
            if self._lock:
                self._lock.release()
                self._lock = None
