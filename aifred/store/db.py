"""sqlite store (I.store). Scratch layer for incremental processing (V13).

Tables:
  messages  — raw ingested msgs (whatsapp/mail), dedup by (channel, ext_id)
  cursors   — last-seen marker per (channel, chat_id) for incremental scan (V13)
  items     — extracted actionable items cache (status: open/done/pushed)

brain.md stays canonical for notes/tasks (V2); this is ephemeral working data.
stdlib sqlite3, no dependency.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    ext_id      TEXT NOT NULL,
    sender      TEXT,
    sender_name TEXT DEFAULT '',
    ts          REAL NOT NULL,
    body        TEXT,
    raw         TEXT,
    UNIQUE(channel, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_msg_scan ON messages(channel, chat_id, ts);

CREATE TABLE IF NOT EXISTS cursors (
    channel   TEXT NOT NULL,
    chat_id   TEXT NOT NULL,
    last_ts   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(channel, chat_id)
);

CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL,
    ref        TEXT,
    created_ts REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,   -- sender | group | domain | category
    pattern    TEXT NOT NULL,   -- value to match (lowercased)
    action     TEXT NOT NULL,   -- mute | vip | high | medium | low
    created_ts REAL NOT NULL DEFAULT 0,
    UNIQUE(scope, pattern)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- WhatsApp @lid -> real phone number, resolved via neonize get_pn_from_lid (V34)
CREATE TABLE IF NOT EXISTS lid_phone (
    lid   TEXT PRIMARY KEY,
    phone TEXT NOT NULL
);

-- in-engine semantic memory: one row per indexed snippet (V31)
CREATE TABLE IF NOT EXISTS embeddings (
    ref    TEXT PRIMARY KEY,   -- source:id, dedup so we embed each snippet once
    source TEXT NOT NULL,      -- chat | whatsapp | attention | inbox
    text   TEXT NOT NULL,
    vec    BLOB NOT NULL,      -- float32 array
    ts     REAL NOT NULL DEFAULT 0
);

-- proposed side effects awaiting owner confirmation over Telegram (V27)
CREATE TABLE IF NOT EXISTS proposals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,            -- 'calendar'
    payload    TEXT NOT NULL,            -- JSON args for the action
    summary    TEXT NOT NULL DEFAULT '', -- human one-liner for the confirm prompt
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | done | error
    source_ref TEXT NOT NULL DEFAULT '', -- dedup key (e.g. selfnote batch)
    created_ts REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL DEFAULT 'New chat',
    created_ts  REAL NOT NULL DEFAULT 0,
    updated_ts  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    ts         REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id, id);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.parent and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: store is shared across threads (whatsapp worker,
        # telegram, web). Access serialized by self._lock (V17).
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        # add columns introduced after first release (sqlite has no IF NOT EXISTS for ADD COLUMN)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(messages)")}
        if "sender_name" not in cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN sender_name TEXT DEFAULT ''")
        if "is_group" not in cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN is_group INTEGER DEFAULT 0")
        if "from_me" not in cols:  # owner's own sent msgs — excluded from triage (V22)
            self.conn.execute("ALTER TABLE messages ADD COLUMN from_me INTEGER DEFAULT 0")
        # meta = JSON rule-target for the active-learning review queue (V21)
        icols = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        if "meta" not in icols:
            self.conn.execute("ALTER TABLE items ADD COLUMN meta TEXT DEFAULT ''")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:  # V17: serialize cross-thread writes
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:  # V17: serialize reads vs concurrent writes
            return list(self.conn.execute(sql, params))

    # --- messages (idempotent ingest, V13) ---
    def add_message(
        self,
        channel: str,
        chat_id: str,
        ext_id: str,
        ts: float,
        body: str = "",
        sender: str = "",
        raw: dict[str, Any] | None = None,
        sender_name: str = "",
        is_group: bool = False,
        from_me: bool = False,
    ) -> bool:
        """Insert msg; returns False if already present (dedup). No re-scan dupes."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO messages(channel,chat_id,ext_id,sender,sender_name,ts,body,raw,is_group,from_me) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (channel, chat_id, ext_id, sender, sender_name, ts, body, json.dumps(raw or {}),
                 int(is_group), int(from_me)),
            )
            return cur.rowcount > 0

    def known_sender_names(self, channel: str = "whatsapp", direct_only: bool = False) -> list[dict]:
        """Distinct (chat_id, sender, sender_name) seen — for contact linking.
        direct_only excludes group messages (a group JID is not a person's number)."""
        sql = ("SELECT DISTINCT chat_id, sender, sender_name FROM messages "
               "WHERE channel=? AND sender_name != ''")
        if direct_only:
            sql += " AND is_group=0 AND from_me=0"
        return [dict(r) for r in self._query(sql, (channel,))]

    def recent_messages(self, channel: str, limit: int = 30, chat_id: str | None = None) -> list[sqlite3.Row]:
        """Most recent N messages (newest last). For the agent's whatsapp tool."""
        if chat_id:
            rows = self._query(
                "SELECT * FROM (SELECT * FROM messages WHERE channel=? AND chat_id=? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id",
                (channel, chat_id, limit),
            )
        else:
            rows = self._query(
                "SELECT * FROM (SELECT * FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (channel, limit),
            )
        return rows

    def chat_summary(self, channel: str) -> list[sqlite3.Row]:
        return self._query(
            "SELECT chat_id, count(*) n, max(ts) last_ts FROM messages WHERE channel=? "
            "GROUP BY chat_id ORDER BY last_ts DESC",
            (channel,),
        )

    def messages_since(self, channel: str, chat_id: str, after_ts: float = 0.0) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM messages WHERE channel=? AND chat_id=? AND ts>? ORDER BY ts",
            (channel, chat_id, after_ts),
        )

    def last_image(self, channel: str = "whatsapp") -> dict | None:
        """Most recent image message (body marked '[obraz]…', V36)."""
        rows = self._query(
            "SELECT ext_id,chat_id,sender,sender_name,body,ts FROM messages "
            "WHERE channel=? AND body LIKE '[obraz]%' ORDER BY id DESC LIMIT 1",
            (channel,),
        )
        return dict(rows[0]) if rows else None

    def messages_after_id(self, last_id: int, channel: str = "whatsapp", limit: int = 200) -> list[sqlite3.Row]:
        """Messages with id > last_id (single global cursor for triage)."""
        return self._query(
            "SELECT * FROM messages WHERE channel=? AND id>? ORDER BY id LIMIT ?",
            (channel, last_id, limit),
        )

    # --- triage rules (learned preferences) ---
    def add_rule(self, scope: str, pattern: str, action: str, ts: float = 0.0) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO rules(scope,pattern,action,created_ts) VALUES(?,?,?,?) "
                "ON CONFLICT(scope,pattern) DO UPDATE SET action=excluded.action",
                (scope, pattern.lower().strip(), action, ts),
            )
            return int(cur.lastrowid)

    def list_rules(self) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM rules ORDER BY id DESC")]

    def delete_rule(self, rule_id: int) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    # --- proposals (confirm-over-telegram side effects, V27) ---
    def add_proposal(self, kind: str, payload: str, summary: str, source_ref: str = "", ts: float = 0.0) -> int:
        if source_ref and self._query("SELECT 1 FROM proposals WHERE source_ref=?", (source_ref,)):
            return 0  # already proposed this exact thing
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO proposals(kind,payload,summary,source_ref,created_ts) VALUES(?,?,?,?,?)",
                (kind, payload, summary, source_ref, ts),
            )
            return int(cur.lastrowid)

    def get_proposal(self, pid: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM proposals WHERE id=?", (pid,))
        return rows[0] if rows else None

    def list_proposals(self, status: str = "pending") -> list[sqlite3.Row]:
        return self._query("SELECT * FROM proposals WHERE status=? ORDER BY id", (status,))

    def set_proposal_status(self, pid: int, status: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE proposals SET status=? WHERE id=?", (status, pid))

    # --- WhatsApp lid -> phone (V34) ---
    def set_lid_phone(self, lid: str, phone: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO lid_phone(lid,phone) VALUES(?,?) ON CONFLICT(lid) DO UPDATE SET phone=excluded.phone",
                      (lid, phone))

    def get_lid_phone(self, lid: str) -> str | None:
        rows = self._query("SELECT phone FROM lid_phone WHERE lid=?", (lid,))
        return rows[0]["phone"] if rows else None

    def lid_phone_map(self) -> dict[str, str]:
        return {r["lid"]: r["phone"] for r in self._query("SELECT lid,phone FROM lid_phone")}

    # --- embeddings (in-engine RAG, V31) ---
    def embedded_refs(self) -> set[str]:
        return {r["ref"] for r in self._query("SELECT ref FROM embeddings")}

    def add_embedding(self, ref: str, source: str, text: str, vec: bytes, ts: float = 0.0) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO embeddings(ref,source,text,vec,ts) VALUES(?,?,?,?,?) "
                "ON CONFLICT(ref) DO NOTHING",
                (ref, source, text, vec, ts),
            )

    def all_embeddings(self) -> list[sqlite3.Row]:
        return self._query("SELECT ref,source,text,vec,ts FROM embeddings")

    def prune_embeddings(self, source: str, keep_refs: set[str]) -> int:
        """Drop rows for a MUTABLE source whose ref is no longer present (handles
        edits/deletions in brain.md done by the owner or another agent, V32)."""
        rows = self._query("SELECT ref FROM embeddings WHERE source=?", (source,))
        stale = [r["ref"] for r in rows if r["ref"] not in keep_refs]
        if stale:
            with self._tx() as c:
                c.executemany("DELETE FROM embeddings WHERE ref=?", [(r,) for r in stale])
        return len(stale)

    # --- app settings (key/value, runtime-tunable) ---
    def get_setting(self, key: str, default: str = "") -> str:
        rows = self._query("SELECT value FROM app_settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO app_settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    # --- cursors (V13 incremental) ---
    def get_cursor(self, channel: str, chat_id: str) -> float:
        rows = self._query("SELECT last_ts FROM cursors WHERE channel=? AND chat_id=?", (channel, chat_id))
        return float(rows[0]["last_ts"]) if rows else 0.0

    def set_cursor(self, channel: str, chat_id: str, last_ts: float) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO cursors(channel,chat_id,last_ts) VALUES(?,?,?) "
                "ON CONFLICT(channel,chat_id) DO UPDATE SET last_ts=excluded.last_ts",
                (channel, chat_id, last_ts),
            )

    def new_messages(self, channel: str, chat_id: str) -> list[sqlite3.Row]:
        """Messages after the cursor; advances cursor to newest. Incremental (V13)."""
        cur = self.get_cursor(channel, chat_id)
        rows = self.messages_since(channel, chat_id, cur)
        if rows:
            self.set_cursor(channel, chat_id, max(r["ts"] for r in rows))
        return rows

    # --- extracted items cache ---
    def add_item(self, source: str, kind: str, content: str, created_ts: float,
                 ref: str = "", status: str = "open", meta: str = "") -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO items(source,kind,content,ref,created_ts,status,meta) VALUES(?,?,?,?,?,?,?)",
                (source, kind, content, ref, created_ts, status, meta),
            )
            return int(cur.lastrowid)

    def list_items(self, status: str = "open") -> list[sqlite3.Row]:
        return self._query("SELECT * FROM items WHERE status=? ORDER BY created_ts", (status,))

    def set_item_status(self, item_id: int, status: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE items SET status=? WHERE id=?", (status, item_id))

    def set_item_kind(self, item_id: int, kind: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE items SET kind=? WHERE id=?", (kind, item_id))

    def set_item_content(self, item_id: int, content: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE items SET content=? WHERE id=?", (content, item_id))

    def message_by_ext(self, ext_id: str, channel: str = "whatsapp") -> dict | None:
        rows = self._query("SELECT sender, sender_name FROM messages WHERE channel=? AND ext_id=?",
                           (channel, ext_id))
        return dict(rows[0]) if rows else None

    # --- triage attention items (dedup by ref = source message id) ---
    def add_attention(self, source: str, importance: str, content: str, ref: str, ts: float,
                      status: str = "open", meta: str = "") -> bool:
        if ref and self._query("SELECT 1 FROM items WHERE ref=?", (ref,)):
            return False  # already triaged this message
        self.add_item(source, importance, content, ts, ref, status=status, meta=meta)
        return True

    def list_attention(self, status: str = "open") -> list[sqlite3.Row]:
        order = "CASE kind WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_ts DESC"
        return self._query(
            f"SELECT * FROM items WHERE source LIKE 'triage%' AND status=? ORDER BY {order}", (status,)
        )

    def get_item(self, item_id: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM items WHERE id=?", (item_id,))
        return rows[0] if rows else None

    # --- chat sessions (persistent, multi-session UI; shared across devices) ---
    def create_session(self, ts: float = 0.0, title: str = "New chat") -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO sessions(title,created_ts,updated_ts) VALUES(?,?,?)", (title, ts, ts)
            )
            return int(cur.lastrowid)

    def list_sessions(self) -> list[dict]:
        rows = self._query(
            "SELECT id,title,created_ts,updated_ts FROM sessions ORDER BY updated_ts DESC, id DESC"
        )
        return [dict(r) for r in rows]

    def session_exists(self, session_id: int) -> bool:
        return bool(self._query("SELECT 1 FROM sessions WHERE id=?", (session_id,)))

    def session_messages(self, session_id: int, limit: int | None = None) -> list[dict]:
        sql = "SELECT role,content,ts FROM chat_messages WHERE session_id=? ORDER BY id"
        params: tuple = (session_id,)
        if limit is not None:
            sql = (
                "SELECT role,content,ts FROM (SELECT * FROM chat_messages WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id"
            )
            params = (session_id, limit)
        return [dict(r) for r in self._query(sql, params)]

    def add_chat_message(self, session_id: int, role: str, content: str, ts: float = 0.0) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO chat_messages(session_id,role,content,ts) VALUES(?,?,?,?)",
                (session_id, role, content, ts),
            )
            c.execute("UPDATE sessions SET updated_ts=? WHERE id=?", (ts, session_id))

    def rename_session(self, session_id: int, title: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))

    def delete_session(self, session_id: int) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
            c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
