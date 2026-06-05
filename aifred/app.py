"""Runtime assembler — wires brain, store, tools, agent, telegram into one app.

Degrades gracefully: google tools load only if a valid (scoped) token exists;
brain tools load only if brain.md is configured. Nothing here is fatal at
startup so the service always comes up (telegram/web reachable) and tells the
user what still needs authorizing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from aifred.agent.loop import AgentLoop
from aifred.agent.router import IntentRouter
from aifred.config import Settings, get_settings
from aifred.confirm import ConfirmManager
from aifred.llm.client import LLMClient
from aifred.mcp.brainmd import BrainMD
from aifred.mcp.client import McpClient
from aifred.skills.journal import build_journal_tools
from aifred.skills.tasks import build_task_tools
from aifred.store.db import Store
from aifred.telegram.bot import TelegramBot, parse_allowed_users
from aifred.tools.base import tool_from_model
from aifred.tools.registry import ToolRegistry

log = logging.getLogger("aifred")

SYSTEM_PROMPT = (
    "You are AIfred, a personal assistant for Owner.\n"
    "Rules:\n"
    "- Use tools to get facts. NEVER invent or guess. If a tool returns nothing, "
    "say plainly you don't have that information — do not fill gaps with assumptions.\n"
    "- NEVER claim you saved/noted/added/created something unless a tool ACTUALLY "
    "returned success in THIS turn. To save a note call journal_add(text=...). If a "
    "tool call errors (e.g. wrong argument), FIX the arguments and call it again — "
    "do not say you did it 'manually' or pretend it worked. No fake confirmations.\n"
    "- Don't invent who a person is from a stray name/nickname in a group. If you "
    "can't resolve them via people_lookup/recall, say you don't know — don't fabricate "
    "a backstory or guess their identity.\n"
    "- A WhatsApp message shown as '[obraz]' is an image. To know what's in it "
    "(read text, see what it shows) call vision_describe — don't guess its content.\n"
    "- For WhatsApp questions use whatsapp_recent / whatsapp_chats (NOT brain.md). "
    "brain.md is for notes/journal/tasks only.\n"
    "- People are referred to by name/alias (e.g. 'Kasia'). To find who that is or "
    "filter their messages, call people_lookup first, then pass sender to "
    "whatsapp_recent. Don't say you found nothing about a person before trying people_lookup.\n"
    "- Do not assume who someone is or what Owner did. 'Owner' in a chat is not "
    "necessarily the user. Report only what the data states; don't infer roles or actions.\n"
    "- For 'what's important / anything to do / today' call attention_list FIRST "
    "(it holds triaged mail+WhatsApp actionables like 'buy X', 'pick up package'); "
    "also check calendar_list for today. brain.md notes are background, not the live to-do.\n"
    "- Different people address Owner by different pet names (e.g. Kasia says 'kotek'). "
    "If the user tells you that someone calls him X, call teach_owner_nickname(person, term). "
    "When such a term appears in that person's message, it is addressed to Owner.\n"
    "- Distinguish an EVENT (you attend) from a REQUEST (someone asked you to do something). "
    "If a message asks you to buy/bring/pick up, report it as a task, not as your plan.\n"
    "- Learn preferences: if the user says to ignore/mute someone or a group, or to "
    "always flag (vip) a person, call triage_rule (action mute/vip, scope sender/group/"
    "domain/category, pattern = the name/group/domain/keyword). Confirm what you set.\n"
    "- For anything spanning past days / 'co było / o czym rozmawialiśmy / wcześniej "
    "wspominał' call recall first — it semantically searches AIfred's own memory "
    "(past chats, WhatsApp, mail, notes). Use its hits as grounding; don't guess.\n"
    "- For a day summary / 'notatka z dnia' / 'co było wczoraj' call daily_note "
    "(it gathers the day's conversations, mail and triage into the fixed brain.md "
    "template). Use journal_add only for a single quick entry.\n"
    "- Quote the source of facts (which tool/chat). Call brain_now for current date/time.\n"
    "- Keep replies terse and in the user's language."
)


@dataclass
class Runtime:
    settings: Settings
    store: Store
    llm: LLMClient
    registry: ToolRegistry
    confirm: ConfirmManager
    agent: AgentLoop
    brain: BrainMD | None = None
    bot: TelegramBot | None = None
    whatsapp: Any = None  # WhatsAppManager | None
    contacts: Any = None  # Contacts | None
    triage: Any = None  # TriageEngine | None
    calendar: Any = None  # Google Calendar service | None
    drive: Any = None  # Google Drive service (read Docs/Sheets links) | None
    rag: Any = None  # RagIndex (in-engine semantic memory) | None
    status: dict[str, str] = field(default_factory=dict)


# --- brain core tools (always available) ---

class BrainCtxArgs(BaseModel):
    query: str = Field(description="what to look up")
    scope: list[str] | None = Field(default=None, description="limit to folders, e.g. ['Journal']")


class EmptyArgs(BaseModel):
    pass


def _build_brain_tools(brain: BrainMD):
    return [
        tool_from_model(
            "brain_context", "retrieve relevant notes from brain.md (token-budgeted)", BrainCtxArgs,
            lambda query, scope: brain.context(query, scope=scope), tags=("core",),
        ),
        tool_from_model(
            "brain_now", "current date/time from brain.md server", EmptyArgs,
            lambda: brain.now(), tags=("core",),
        ),
    ]


def _load_google_creds(settings: Settings, status: dict):
    from aifred.google.auth import NeedsReauth, load_credentials

    if not settings.google_token_path:
        status["google"] = "no token path configured"
        return None
    try:
        return load_credentials(settings.google_token_path)
    except NeedsReauth as e:
        status["google"] = f"needs auth: {e}"
        return None


def _try_google_tools(creds, registry: ToolRegistry, llm, store, status: dict):
    """Register gmail/calendar/digest tools. Returns (people, gmail, calendar, drive)."""
    if creds is None:
        return None, None, None, None
    try:
        from googleapiclient.discovery import build

        from aifred.google.tools import build_google_tools
        from aifred.skills.digest import build_digest_tool

        gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
        for t in build_google_tools(gmail, cal):
            registry.register(t)
        for t in build_digest_tool(gmail, cal, store, llm):
            registry.register(t)
        drive = None
        try:  # drive.readonly is a newer scope — degrade if not yet granted (re-auth)
            drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception:  # noqa: BLE001
            drive = None
        status["google"] = "ok"
        people = build("people", "v1", credentials=creds, cache_discovery=False)
        return people, gmail, cal, drive
    except Exception as e:  # noqa: BLE001 — never fatal at startup
        status["google"] = f"build failed: {e}"
        return None, None, None, None


def build_runtime(settings: Settings | None = None) -> Runtime:
    s = settings or get_settings()
    status: dict[str, str] = {}

    store = Store(f"{s.data_dir}/aifred.db")
    llm = LLMClient(s)
    registry = ToolRegistry()

    brain: BrainMD | None = None
    if s.brainmd_mcp_url and s.brainmd_api_key:
        client = McpClient(s.brainmd_mcp_url, headers={"Authorization": f"Bearer {s.brainmd_api_key}"})
        brain = BrainMD(client=client)
        try:
            for t in _build_brain_tools(brain):
                registry.register(t)
            for t in build_journal_tools(brain, llm):
                registry.register(t)
            for t in build_task_tools(store, brain):
                registry.register(t)
            status["brain"] = "ok"
        except Exception as e:  # noqa: BLE001
            status["brain"] = f"error: {e}"
    else:
        status["brain"] = "not configured"

    google_creds = _load_google_creds(s, status)
    people_service, gmail_service, calendar_service, drive_service = _try_google_tools(google_creds, registry, llm, store, status)

    # identity layer: name/alias <-> number/jid/email, from brain.md ludzie/ + WA
    # pushnames + Google Contacts (phone links WA jids, even old messages)
    contacts = None
    if brain is not None:
        from aifred.contacts import Contacts

        contacts = Contacts(
            brain, store,
            owner_name=s.owner_aliases.split(",")[0].strip() + " (ja)",
            owner_lid=s.owner_whatsapp_lid, owner_phone=s.owner_whatsapp,
        )
        try:
            contacts.load()
            gc = contacts.load_google(people_service) if people_service is not None else 0
            linked = contacts.link_google() if gc else 0
            status["contacts"] = f"{len(contacts.people)} people, {gc} google contacts, {linked} linked"
        except Exception as e:  # noqa: BLE001
            status["contacts"] = f"error: {e}"

    # whatsapp read tools + people lookup — agent accesses messages and resolves identities
    from aifred.skills.whatsapp_query import build_whatsapp_tools

    for t in build_whatsapp_tools(store, contacts):
        registry.register(t)

    # attention tool — agent reads triage output ("what's important today")
    from aifred.skills.attention import build_attention_tool

    for t in build_attention_tool(store, contacts):
        registry.register(t)

    # triage rule tools — teaching/correcting importance preferences
    from aifred.skills.rules import build_rule_tools

    for t in build_rule_tools(store):
        registry.register(t)

    # read Google Docs/Sheets links (Drive export) — needs drive.readonly scope
    if drive_service is not None:
        from pydantic import BaseModel, Field

        from aifred.google.gdocs import read_url
        from aifred.tools.base import tool_from_model

        class GDocArgs(BaseModel):
            url: str = Field(description="link do Google Docs/Sheets/Slides")

        registry.register(tool_from_model(
            "gdoc_read", "odczytaj zawartość linku Google Docs/Sheets/Slides", GDocArgs,
            lambda url: read_url(drive_service, url), tags=("google",),
        ))

    # in-engine semantic memory (RAG) — recall across days/sources without dumping
    rag = None
    if s.rag_enabled:
        from pydantic import BaseModel, Field

        from aifred.rag.embedder import Embedder
        from aifred.rag.index import RagIndex
        from aifred.tools.base import tool_from_model

        rag = RagIndex(store, Embedder(s.ollama_base_url, s.embed_model), contacts=contacts, brain=brain)

        class RecallArgs(BaseModel):
            query: str = Field(description="czego szukasz w pamięci (rozmowy, WhatsApp, maile, notatki)")
            k: int = Field(default=5, ge=1, le=10)

        def _recall(query: str, k: int) -> dict:
            try:
                return {"hits": rag.recall(query, k)}
            except Exception as e:  # noqa: BLE001
                return {"hits": [], "error": str(e)[:120]}

        registry.register(tool_from_model(
            "recall", "przeszukaj pamięć AIfred (rozmowy/WhatsApp/maile/notatki z poprzednich dni)",
            RecallArgs, _recall, tags=("memory", "core"),
        ))

    # vision on-demand — read a WhatsApp image with a VL model only when asked (V36)
    if s.vision_enabled:
        import os as _os

        from pydantic import BaseModel, Field

        from aifred.tools.base import tool_from_model
        from aifred.vision import describe_image

        class VisionArgs(BaseModel):
            ext_id: str | None = Field(default=None, description="id obrazu; pusty = ostatni przysłany obraz")

        def _vision(ext_id: str | None) -> dict:
            target = ext_id
            if not target:
                img = store.last_image()
                if not img:
                    return {"error": "brak obrazów w historii"}
                target = img["ext_id"]
            path = f"{s.data_dir}/media/{target}.jpg"
            if not _os.path.exists(path):
                return {"error": "obraz niedostępny (nie pobrany)"}
            txt = describe_image(path, model=s.vision_model, base_url=s.ollama_base_url)
            return {"ext_id": target, "description": txt or "(nie udało się opisać)"}

        registry.register(tool_from_model(
            "vision_describe", "opisz/odczytaj obraz z WhatsApp (ostatni lub po ext_id) — model wizyjny",
            VisionArgs, _vision, tags=("vision", "core"),
        ))

    # daily-note composer — structured "notatka z dnia" (writes the template too)
    if brain is not None:
        from aifred.skills.daily_note import build_daily_note_tool

        for t in build_daily_note_tool(brain, llm, store, contacts):
            registry.register(t)

    # proactive triage engine (mail + whatsapp -> attention). Scheduler in service.
    from aifred.triage import TriageEngine

    triage = TriageEngine(
        store, llm, contacts=contacts, gmail_service=gmail_service,
        owner_aliases=s.owner_aliases, owner_email=s.owner_email, owner_lid=s.owner_whatsapp_lid,
        owner_phone=s.owner_whatsapp, brain=brain,
    )

    confirm = ConfirmManager(mode="ask")
    router = IntentRouter(registry)
    agent = AgentLoop(
        llm=llm, registry=registry, router=router,
        system_prompt=SYSTEM_PROMPT, confirm=confirm.hook,
    )

    bot: TelegramBot | None = None
    if s.telegram_bot_token:
        bot = TelegramBot(
            token=s.telegram_bot_token,
            allowed_users=parse_allowed_users(s.telegram_allowed_users),
            agent=agent,
        )
        status["telegram"] = f"{len(bot.allowed_users)} allowed user(s)"
    else:
        status["telegram"] = "no token"

    # whatsapp manager (pairing via web UI). Always available so UI can enable it.
    from aifred.whatsapp.pairing import WhatsAppManager

    import re as _re

    wa = WhatsAppManager(
        store=store,
        session_path=s.whatsapp_session_path or f"{s.data_dir}/wa.sqlite",
        lock_path=f"{s.data_dir}/wa.lock",
        agent=agent,  # owner WhatsApp commands ("Alfredzie, ...")
        owner_tail=_re.sub(r"\D", "", s.owner_whatsapp)[-9:],
        media_dir=f"{s.data_dir}/media",  # V36: downloaded images for on-demand vision
    )
    status["whatsapp"] = "paired" if wa.status()["paired"] else "not paired (enable in web UI)"

    log.info("aifred runtime: %s | tools=%s", status, registry.names())
    return Runtime(
        settings=s, store=store, llm=llm, registry=registry, confirm=confirm,
        agent=agent, brain=brain, bot=bot, whatsapp=wa, contacts=contacts,
        triage=triage, calendar=calendar_service, drive=drive_service, rag=rag, status=status,
    )
