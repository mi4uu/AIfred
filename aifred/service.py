"""AIfred always-on service entrypoint.

Starts the web API (uvicorn) + telegram long-poll (background thread) from one
process. Run via systemd (see deploy/aifred.service). Telegram is the push
channel too: notify() sends to the configured home channel.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import uvicorn

from aifred.app import Runtime, build_runtime
from aifred.config import get_settings
from aifred.main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# V8: httpx logs full URLs incl. the telegram bot token at INFO — silence it
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("aifred.service")

WARMUP_INTERVAL_S = 1200  # 20 min — under the 30m keep_alive so model stays resident


def _warmup_loop(rt: Runtime) -> None:  # pragma: no cover (long-running)
    while True:
        ok = rt.llm.warmup()
        log.info("model warmup %s", "ok" if ok else "failed")
        time.sleep(WARMUP_INTERVAL_S)


def notify(rt: Runtime, text: str) -> bool:
    """Push a message to the telegram home channel (notifications)."""
    chan = rt.settings.telegram_home_channel
    if not rt.bot or not chan:
        return False
    try:
        rt.bot.send_message(int(chan), text)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("notify failed: %s", e)
        return False


TRIAGE_TICK_S = 60  # how often the loop checks the interval setting


def _triage_loop(rt: Runtime) -> None:  # pragma: no cover (long-running)
    """Run triage every `triage_interval_min` (0 = off, tunable from UI)."""
    elapsed = 0
    while True:
        time.sleep(TRIAGE_TICK_S)
        elapsed += TRIAGE_TICK_S
        try:
            interval_min = int(rt.store.get_setting("triage_interval_min", "0"))
        except ValueError:
            interval_min = 0
        if interval_min <= 0 or elapsed < interval_min * 60:
            continue
        elapsed = 0
        try:
            result = rt.triage.run()
            if result["high"]:
                notify(rt, "🔔 Wymaga uwagi:\n" + "\n".join(f"• {h}" for h in result["high"][:10]))
            if result.get("review"):
                notify(rt, f"❓ {result['review']} do decyzji w panelu „Do decyzji”.")
            if result.get("notes"):
                notify(rt, f"📝 Zapisałem {result['notes']} notatkę/i z WhatsApp (do siebie) w brain.md.")
        except Exception as e:  # noqa: BLE001
            log.warning("triage run failed: %s", e)
        # re-sync with brain.md — owner or another agent may have edited it (V32)
        _resync_brain(rt)
        # self-maintenance — keep the contact book + journal current without me (V24)
        _contacts_writeback(rt)
        _daily_note_maybe(rt)
        try:  # re-judge frozen attention items: guards + identity upgrades (V37)
            rt.triage.rejudge_open()
        except Exception as e:  # noqa: BLE001
            log.warning("rejudge_open failed: %s", e)
        if rt.rag:  # keep semantic memory current (V31); prunes brain edits (V32)
            try:
                rt.rag.refresh()
            except Exception as e:  # noqa: BLE001
                log.warning("rag refresh failed: %s", e)


def _resync_brain(rt: Runtime) -> None:
    """Re-parse the contact book from brain.md so external edits (new number,
    nickname, person added via the UI or another agent) are picked up live."""
    if not rt.contacts:
        return
    try:
        before = len(rt.contacts.people)
        rt.contacts.load()
        if rt.contacts.google_contacts:  # re-apply Google enrichment after reload (V33)
            rt.contacts.link_google()
        after = len(rt.contacts.people)
        if after != before:
            log.info("brain re-sync: contacts %s -> %s people", before, after)
    except Exception as e:  # noqa: BLE001
        log.warning("brain re-sync failed: %s", e)


def _contacts_writeback(rt: Runtime) -> None:
    """Fill confirmed WhatsApp numbers into ludzie/ on unambiguous matches."""
    if not (rt.settings.contacts_writeback and rt.contacts):
        return
    try:
        changes = rt.contacts.auto_writeback()
    except Exception as e:  # noqa: BLE001
        log.warning("contacts writeback failed: %s", e)
        return
    for ch in changes:
        log.info("contacts: linked %s -> %s", ch["person"], ch["number"])
        notify(rt, f"📇 Zapisałem numer: {ch['person']} → {ch['number']}")


def _daily_note_maybe(rt: Runtime) -> None:
    """Once per day, after the configured hour, compose yesterday's note."""
    s = rt.settings
    if not (s.daily_note_enabled and rt.brain and rt.contacts):
        return
    now = datetime.now(timezone.utc)
    if now.hour < s.daily_note_hour:
        return
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if rt.store.get_setting("daily_note_last", "") == yesterday:
        return  # already written
    try:
        from aifred.skills.daily_note import compose_daily_note

        res = compose_daily_note(rt.brain, rt.llm, rt.store, rt.contacts, yesterday)
        rt.store.set_setting("daily_note_last", yesterday)  # mark done even if empty (don't retry all day)
        if res.get("written"):
            log.info("daily note written for %s", yesterday)
            notify(rt, f"📓 Notatka z {yesterday} gotowa.")
    except Exception as e:  # noqa: BLE001
        log.warning("daily note failed: %s", e)


def _telegram_loop(rt: Runtime) -> None:  # pragma: no cover (long-running)
    if not rt.bot:
        log.info("telegram disabled (no token)")
        return
    log.info("telegram polling started")
    rt.bot.run()


def _make_self_note_proposer(rt: Runtime):
    """Turn captured self-notes into confirm-over-Telegram calendar proposals (V27)."""
    s = rt.settings

    def proposer(text: str, batch_ref: str) -> int:
        from aifred.skills.proposals import brain_web_link, propose_events

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # if the note links a Google Doc/Sheet, pull its real content so events
        # come from the actual schedule, not just the chat line (V29)
        if rt.drive is not None:
            try:
                from aifred.google.gdocs import fetch_all

                for doc in fetch_all(rt.drive, text):
                    text += f"\n\n[Arkusz/dokument „{doc['title']}”]:\n{doc['text']}"
                    log.info("gdoc fetched for proposals: %s", doc["title"])
            except Exception as e:  # noqa: BLE001
                log.warning("gdoc fetch failed: %s", e)
        link = brain_web_link(s.brainmd_web_url, s.brainmd_mcp_url, "Journal/inbox.md")
        source = f"WA→ja {today} (brain.md Journal/inbox.md)"
        made = propose_events(rt.store, rt.llm, text, today, source, link, batch_ref)
        chan = s.telegram_home_channel
        if made and rt.bot and chan:
            # lead with the source note so the owner sees WHY, not a bare "set this"
            head = text.strip().replace("\n", " ")
            if len(head) > 320:
                head = head[:320] + "…"
            rt.bot.send_message(int(chan), f"📥 Z Twojej notatki (WA→ja):\n„{head}”\n\n→ {len(made)} propozycji do kalendarza:")
            for m in made:
                body = m["line"]
                if m.get("evidence"):
                    body += f"\n📄 {m['evidence']}"
                if m.get("link"):
                    body += f"\n🔗 {m['link']}"
                rt.bot.send_proposal(int(chan), body, m["id"])
        return len(made)

    return proposer


def run_service() -> None:  # pragma: no cover (entrypoint)
    s = get_settings()
    rt = build_runtime(s)
    log.info("startup status: %s", rt.status)

    # confirm-over-Telegram: resolver executes proposals on ✅; self-notes -> proposals (V27)
    if rt.bot:
        from aifred.skills.proposals import CalendarProposalResolver

        rt.bot.callback_resolver = CalendarProposalResolver(rt.store, rt.calendar)
    if rt.triage:
        rt.triage.on_self_notes = _make_self_note_proposer(rt)

    # keep the local model warm so web/telegram chats never cold-load (avoids cloudflare 502)
    threading.Thread(target=_warmup_loop, args=(rt,), daemon=True, name="warmup").start()
    threading.Thread(target=_triage_loop, args=(rt,), daemon=True, name="triage").start()
    if rt.bot:
        threading.Thread(target=_telegram_loop, args=(rt,), daemon=True, name="telegram").start()
    # whatsapp: auto-connect if already paired; otherwise enable via web UI (shows QR)
    if rt.whatsapp and rt.whatsapp.status()["paired"]:
        rt.whatsapp.start()
    elif s.whatsapp_enabled:
        log.info("whatsapp enabled but not paired — open the web UI to scan the QR")

    app = create_app(agent=rt.agent, confirm=rt.confirm, whatsapp=rt.whatsapp, store=rt.store,
                     triage=rt.triage, contacts=rt.contacts)
    uvicorn.run(app, host=s.web_host, port=s.web_port, log_level="info")


if __name__ == "__main__":
    run_service()
