"""WhatsApp query + people tools (I.whatsapp) — agent READS messages + resolves
identities (name/alias <-> number/jid via the Contacts layer).

Without these the agent had no WhatsApp access and couldn't tell that "Kasia"
== a phone number, so it answered from brain.md and hallucinated.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from aifred.store.db import Store
from aifred.tools.base import Tool, tool_from_model

RECENT_MAX = 50


def _fmt(rows, contacts=None) -> list[dict]:
    out = []
    for r in rows:
        name = r["sender"]
        if contacts is not None:
            name = contacts.name_for(r["sender"], r["sender_name"] or "")
        elif r["sender_name"]:
            name = r["sender_name"]
        out.append({"chat": r["chat_id"], "from": name, "text": r["body"]})
    return out


def whatsapp_recent(store: Store, limit: int = 30, query: str | None = None, sender: str | None = None, contacts=None) -> dict:
    """Recent WhatsApp messages. `query` = text filter; `sender` = person/name filter."""
    rows = store.recent_messages("whatsapp", limit=min(limit, RECENT_MAX))
    msgs = _fmt(rows, contacts)
    if sender and contacts is not None:
        jids = contacts.jids_for(sender)
        slow = sender.lower()
        msgs = [m for m, r in zip(msgs, rows) if r["sender"] in jids or slow in (m["from"] or "").lower()]
    elif sender:
        slow = sender.lower()
        msgs = [m for m in msgs if slow in (m["from"] or "").lower()]
    if query:
        ql = query.lower()
        msgs = [m for m in msgs if ql in (m["text"] or "").lower()]
    if not msgs:
        return {"messages": [], "note": "no matching WhatsApp messages in the store"}
    return {"messages": msgs, "count": len(msgs)}


def whatsapp_chats(store: Store, contacts=None) -> dict:
    rows = store.chat_summary("whatsapp")
    chats = []
    for r in rows:
        name = contacts.name_for(r["chat_id"]) if contacts is not None else r["chat_id"]
        chats.append({"chat": r["chat_id"], "name": name, "messages": r["n"]})
    return {"chats": chats}


def people_lookup(contacts, query: str) -> dict:
    if contacts is None:
        return {"found": False, "note": "contacts not available"}
    return contacts.describe(query)


class RecentArgs(BaseModel):
    limit: int = Field(default=30, ge=1, le=RECENT_MAX)
    query: str | None = Field(default=None, description="optional substring to filter message text")
    sender: str | None = Field(default=None, description="optional person name/alias to filter by (e.g. 'Kasia')")


class PeopleArgs(BaseModel):
    query: str = Field(description="person name or alias to look up (e.g. 'Kasia')")


class EmptyArgs(BaseModel):
    pass


class NicknameArgs(BaseModel):
    person: str = Field(description="osoba (imię/alias), np. 'Kasia'")
    term: str = Field(description="jak ta osoba zwraca się do właściciela, np. 'kotek'")


def teach_owner_nickname(contacts, person: str, term: str) -> dict:
    """Record that `person` calls the owner `term` (e.g. Kasia -> kotek). Persisted
    to brain.md so triage knows such a message is addressed to the owner."""
    if contacts is None:
        return {"ok": False, "reason": "contacts not available"}
    return contacts.teach_owner_term(person, term)


def build_whatsapp_tools(store: Store, contacts=None) -> list[Tool]:
    tools = [
        tool_from_model(
            "whatsapp_recent", "read recent WhatsApp messages; filter by text and/or person", RecentArgs,
            lambda limit, query, sender: whatsapp_recent(store, limit, query, sender, contacts), tags=("whatsapp",),
        ),
        tool_from_model(
            "whatsapp_chats", "list WhatsApp chats AIfred has messages from", EmptyArgs,
            lambda: whatsapp_chats(store, contacts), tags=("whatsapp",),
        ),
    ]
    if contacts is not None:
        tools.append(
            tool_from_model(
                "people_lookup", "resolve a person name/alias to role + WhatsApp/email/phone", PeopleArgs,
                lambda query: people_lookup(contacts, query), tags=("whatsapp", "core"),
            )
        )
        tools.append(
            tool_from_model(
                "teach_owner_nickname",
                "zapamiętaj, że dana osoba zwraca się do właściciela danym pseudonimem (np. Kasia -> kotek)",
                NicknameArgs,
                lambda person, term: teach_owner_nickname(contacts, person, term), tags=("whatsapp", "core"),
            )
        )
    return tools
