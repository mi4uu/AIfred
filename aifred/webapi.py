"""Web API (I.web). Chat (SSE) + sessions + confirm, token auth, localhost (C6).

Chat streams over SSE (text/event-stream) so long agent runs never hit the
cloudflare ~100s proxy timeout (the connection keeps emitting events) and the
UI shows live activity: which tool is running, that it's thinking, the reply.

Sessions are persistent + multi (new/list/switch/delete) like Claude/Gemini web.
If AIFRED_WEB_TOKEN is set it's required on every /api/* call.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from aifred.confirm import ConfirmManager

HEARTBEAT_S = 10  # SSE keep-alive ping interval (defeats proxy idle timeout)


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None


class ConfirmIn(BaseModel):
    token: str
    approve: bool = True


class RenameIn(BaseModel):
    title: str


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


class SettingsIn(BaseModel):
    triage_interval_min: int | None = None
    triage_enabled: bool | None = None


def make_api(
    agent: Any,
    confirm: ConfirmManager,
    web_token: str = "",
    whatsapp: Any | None = None,
    store: Any | None = None,
    triage: Any | None = None,
    contacts: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def auth(x_aifred_token: str = Header(default="")) -> None:
        if web_token and x_aifred_token != web_token:
            raise HTTPException(status_code=401, detail="bad token")

    def _need_store():
        if store is None:
            raise HTTPException(status_code=503, detail="store not configured")

    # ---- sessions ----
    @router.get("/sessions", dependencies=[Depends(auth)])
    def list_sessions() -> dict:
        _need_store()
        return {"sessions": store.list_sessions()}

    @router.post("/sessions", dependencies=[Depends(auth)])
    def new_session() -> dict:
        _need_store()
        sid = store.create_session(ts=time.time())
        return {"id": sid, "title": "New chat"}

    @router.get("/sessions/{sid}/messages", dependencies=[Depends(auth)])
    def session_messages(sid: int) -> dict:
        _need_store()
        if not store.session_exists(sid):
            raise HTTPException(status_code=404, detail="no such session")
        return {"messages": store.session_messages(sid)}

    @router.patch("/sessions/{sid}", dependencies=[Depends(auth)])
    def rename_session(sid: int, body: RenameIn) -> dict:
        _need_store()
        store.rename_session(sid, body.title[:80])
        return {"status": "ok"}

    @router.delete("/sessions/{sid}", dependencies=[Depends(auth)])
    def delete_session(sid: int) -> dict:
        _need_store()
        store.delete_session(sid)
        return {"status": "deleted"}

    # ---- chat (SSE) ----
    @router.post("/chat/stream", dependencies=[Depends(auth)])
    def chat_stream(body: ChatIn) -> StreamingResponse:
        _need_store()
        now = time.time()
        sid = body.session_id
        if not sid or not store.session_exists(sid):
            sid = store.create_session(ts=now)
        history = store.session_messages(sid, limit=20)
        first_turn = len(history) == 0
        store.add_chat_message(sid, "user", body.message, now)
        if first_turn:
            store.rename_session(sid, body.message[:60])

        q: queue.Queue = queue.Queue()

        def on_step(event: str, detail: str = "") -> None:
            q.put({"type": "status", "event": event, "detail": detail})

        def work() -> None:
            try:
                res = agent.run(body.message, history=history, on_step=on_step)
                reply = res.get("reply", "") if isinstance(res, dict) else str(res)
                store.add_chat_message(sid, "assistant", reply, time.time())
                q.put({
                    "type": "reply",
                    "reply": reply,
                    "session_id": sid,
                    "pending": [p.__dict__ for p in confirm.list_pending()],
                })
            except Exception as e:  # noqa: BLE001 — surface to client (V9)
                q.put({"type": "error", "error": str(e)})
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True, name=f"chat-{sid}").start()

        def gen():
            yield _sse({"type": "session", "session_id": sid})
            while True:
                try:
                    item = q.get(timeout=HEARTBEAT_S)
                except queue.Empty:
                    yield ": keep-alive\n\n"  # SSE comment heartbeat
                    continue
                if item is None:
                    break
                yield _sse(item)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- non-streaming chat (kept for simple clients / tests) ----
    @router.post("/chat", dependencies=[Depends(auth)])
    def chat(body: ChatIn) -> dict:
        result = agent.run(body.message)
        reply = result.get("reply", "") if isinstance(result, dict) else str(result)
        return {"reply": reply, "pending": [p.__dict__ for p in confirm.list_pending()]}

    # ---- confirm (V7) ----
    @router.get("/pending", dependencies=[Depends(auth)])
    def pending() -> dict:
        return {"pending": [p.__dict__ for p in confirm.list_pending()]}

    @router.post("/confirm", dependencies=[Depends(auth)])
    def do_confirm(body: ConfirmIn) -> dict:
        try:
            p = confirm.approve(body.token)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown token")
        if body.approve:
            confirm.pre_authorize(p.tool)  # allow retried call (V7)
            return {"status": "approved", "tool": p.tool}
        return {"status": "rejected", "tool": p.tool}

    # ---- settings (runtime-tunable triage cadence) ----
    @router.get("/settings", dependencies=[Depends(auth)])
    def get_settings_ep() -> dict:
        _need_store()
        return {
            "triage_interval_min": int(store.get_setting("triage_interval_min", "0") or 0),
        }

    @router.post("/settings", dependencies=[Depends(auth)])
    def set_settings_ep(body: SettingsIn) -> dict:
        _need_store()
        if body.triage_interval_min is not None:
            store.set_setting("triage_interval_min", str(max(0, body.triage_interval_min)))
        if body.triage_enabled is not None and not body.triage_enabled:
            store.set_setting("triage_interval_min", "0")
        return {"triage_interval_min": int(store.get_setting("triage_interval_min", "0") or 0)}

    # ---- attention feed (triage output) ----
    @router.get("/attention", dependencies=[Depends(auth)])
    def attention() -> dict:
        _need_store()
        from aifred.skills.attention import _resolve_identities

        rows = store.list_attention("open")
        return {"items": [{"id": r["id"], "importance": r["kind"],
                           "text": _resolve_identities(r["content"], contacts), "ts": r["created_ts"]} for r in rows]}

    @router.post("/attention/{item_id}/done", dependencies=[Depends(auth)])
    def attention_done(item_id: int) -> dict:
        _need_store()
        store.set_item_status(item_id, "done")
        return {"status": "done"}

    @router.post("/triage/run", dependencies=[Depends(auth)])
    def triage_run() -> dict:
        if triage is None:
            raise HTTPException(status_code=503, detail="triage not configured")
        return triage.run()

    # ---- review queue (active learning: uncertain item -> owner decides -> rule) ----
    @router.get("/review", dependencies=[Depends(auth)])
    def review_list() -> dict:
        _need_store()
        import json as _j

        out = []
        for r in store.list_attention("review"):
            try:
                meta = _j.loads(r["meta"] or "{}")
            except (ValueError, TypeError):
                meta = {}
            out.append({
                "id": r["id"], "text": r["content"], "suggest": meta.get("suggest", r["kind"]),
                "ts": r["created_ts"], "person": meta.get("person", ""),
                "is_group": meta.get("is_group", False),
            })
        return {"items": out}

    @router.post("/review/{item_id}", dependencies=[Depends(auth)])
    def review_decide(item_id: int, body: dict) -> dict:
        """Owner verdict on an uncertain item. Records a scoped rule so the same
        sender/domain is auto-handled next time, then files the item (V21).
        decision: important | not_important | mute."""
        _need_store()
        import json as _j
        import time as _t

        decision = str(body.get("decision", "")).lower()
        if decision not in ("important", "not_important", "mute"):
            raise HTTPException(status_code=400, detail="bad decision")
        row = store.get_item(item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such item")
        try:
            meta = _j.loads(row["meta"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        # pick the narrowest stable target: group -> group jid, mail -> domain, else sender/person
        if meta.get("is_group"):
            scope, pattern = "group", meta.get("sender", "")
        elif meta.get("source") == "mail" and meta.get("domain"):
            scope, pattern = "domain", meta.get("domain", "")
        else:
            scope, pattern = "sender", meta.get("person") or meta.get("sender", "")
        action = {"important": "vip", "not_important": "low", "mute": "mute"}[decision]
        rule_id = None
        if pattern:
            rule_id = store.add_rule(scope, pattern, action, _t.time())
        # important -> keep it visible as high; otherwise file it away
        if decision == "important":
            store.set_item_kind(item_id, "high")  # promote so the feed shows it
            store.set_item_status(item_id, "open")
        else:
            store.set_item_status(item_id, "done")
        return {"status": "ok", "rule_id": rule_id, "scope": scope, "pattern": pattern, "action": action}

    # ---- triage rules (teaching) ----
    @router.get("/rules", dependencies=[Depends(auth)])
    def rules_list() -> dict:
        _need_store()
        return {"rules": store.list_rules()}

    @router.post("/rules", dependencies=[Depends(auth)])
    def rules_add(body: dict) -> dict:
        _need_store()
        import time as _t

        scope = str(body.get("scope", "")).lower()
        action = str(body.get("action", "")).lower()
        pattern = str(body.get("pattern", "")).strip()
        if scope not in ("sender", "group", "domain", "category") or action not in (
            "mute", "vip", "high", "medium", "low"
        ) or not pattern:
            raise HTTPException(status_code=400, detail="bad rule")
        rid = store.add_rule(scope, pattern, action, _t.time())
        return {"id": rid}

    @router.delete("/rules/{rule_id}", dependencies=[Depends(auth)])
    def rules_delete(rule_id: int) -> dict:
        _need_store()
        store.delete_rule(rule_id)
        return {"status": "deleted"}

    # ---- whatsapp ----
    @router.get("/whatsapp/status", dependencies=[Depends(auth)])
    def wa_status() -> dict:
        if whatsapp is None:
            return {"state": "unavailable", "qr": None, "paired": False}
        return whatsapp.status()

    @router.post("/whatsapp/start", dependencies=[Depends(auth)])
    def wa_start() -> dict:
        if whatsapp is None:
            raise HTTPException(status_code=503, detail="whatsapp not configured")
        return whatsapp.start()

    return router
