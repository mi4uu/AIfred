"""Identity / contacts layer.

Maps how Owner calls people (name + aliases) <-> their identifiers (WhatsApp
JID/number, email, phone). Canonical source = brain.md `ludzie/` notes (the
hermes convention). JIDs get linked automatically from WhatsApp PushNames seen
in the store: when a message's PushName matches a person's alias, that JID is
attached to the person — so "co pisała Kasia" can resolve to her chat.

Google People (emails/phones) can enrich this later once OAuth is done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from aifred.mcp.brainmd import BrainMD
from aifred.store.db import Store

PEOPLE_FOLDER = "ludzie"
_TOKEN = re.compile(r"[A-Za-zÀ-ÿĄ-ż]{3,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s\-]{7,}\d")
_JID = re.compile(r"\d{6,}(?:@[\w.]+)?")
_STOP = {"title", "rola", "dziewczyna", "ownera", "michał", "ownera", "tags", "ludzie", "rodzina"}
# how a person addresses the owner (pet name) — note line "Mówi do mnie: kotek, kotku"
_OWNER_TERMS = re.compile(
    r"(?:mówi do mnie|nazywa mnie|zwraca się do mnie|pseudonim dla mnie|mój pseudonim)\s*[:\-]?\s*(.+)",
    re.IGNORECASE,
)


@dataclass
class Person:
    slug: str
    name: str
    aliases: set[str] = field(default_factory=set)
    role: str = ""
    jids: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    path: str = ""  # brain.md note path — for auto write-back
    owner_terms: set[str] = field(default_factory=set)  # how THIS person addresses the owner (e.g. Kasia -> "kotek")

    def matches(self, query: str) -> bool:
        q = query.lower().strip()
        if not q:
            return False
        qtok = set(_TOKEN.findall(q))
        return bool(self.aliases & qtok) or any(a in q or q in a for a in self.aliases)


def _title_of(content: str, slug: str) -> str:
    m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else slug


def parse_person(slug: str, content: str) -> Person:
    title = _title_of(content, slug)
    aliases = {t.lower() for t in _TOKEN.findall(title)} - _STOP
    aliases.add(slug.lower())
    role_m = re.search(r"\*\*Rola:\*\*\s*(.+)", content) or re.search(r"Rola:\s*(.+)", content)
    role = role_m.group(1).strip() if role_m else ""
    emails = set(_EMAIL.findall(content))
    # only treat ## Kontakt-ish lines as phone/jid sources to avoid dates etc.
    phones = {p.strip() for p in _PHONE.findall(content) if len(re.sub(r"\D", "", p)) >= 9}
    jids = {j for j in _JID.findall(content) if "@" in j}
    owner_terms = _parse_owner_terms(content)
    return Person(slug=slug, name=title, aliases=aliases, role=role, emails=emails, phones=phones,
                  jids=jids, owner_terms=owner_terms)


def _parse_owner_terms(content: str) -> set[str]:
    """Pet names this person uses for the OWNER (e.g. Kasia -> kotek)."""
    out: set[str] = set()
    for m in _OWNER_TERMS.finditer(content):
        raw = m.group(1)
        raw = re.sub(r"[*_`]", "", raw)  # strip md emphasis
        for tok in re.split(r"[,/;]| i ", raw):
            t = tok.strip().strip(".\"' ").lower()
            if 2 <= len(t) <= 24 and " " not in t:
                out.add(t)
    return out


class Contacts:
    def __init__(self, brain: BrainMD, store: Store, owner_name: str = "", owner_lid: str = "", owner_phone: str = ""):
        self.brain = brain
        self.store = store
        self.people: list[Person] = []
        self.google_contacts: list[dict] = []
        self.owner_name = owner_name or "Owner (ja)"
        # owner's own WA identities (the @lid + phone) — so own messages aren't a phantom contact
        self.owner_ids = {self._digits(x) for x in (owner_lid, owner_phone) if self._digits(x)}
        self.lid_map: dict[str, str] = {}  # WhatsApp @lid -> real phone (V34)

    def _resolved_tail(self, sender: str) -> str:
        """Last-9 digits of a sender's REAL phone — resolving @lid -> phone (V34)
        so Google contacts (keyed by phone) can match WhatsApp senders."""
        base = self.lid_map.get(self._digits(sender)) or sender
        return self._digits(base)[-9:]

    def is_owner(self, sender: str) -> bool:
        d = self._digits(sender)
        return bool(d) and (d in self.owner_ids or d[-9:] in {o[-9:] for o in self.owner_ids if len(o) >= 9})

    @staticmethod
    def _digits(s: str) -> str:
        return re.sub(r"\D", "", s or "")

    def load(self) -> None:
        """Parse brain.md ludzie/ notes. Safe to call repeatedly (refresh)."""
        import json

        try:
            listing = json.loads(self.brain.list_notes(PEOPLE_FOLDER))
        except Exception:  # noqa: BLE001
            return
        notes = [n for n in listing.get("notes", []) if n.lower() != f"{PEOPLE_FOLDER}/index.md"]
        people: list[Person] = []
        for path in notes:
            slug = path.split("/")[-1].rsplit(".", 1)[0]
            try:
                content = self.brain.read(path)
            except Exception:  # noqa: BLE001
                continue
            person = parse_person(slug, content)
            person.path = path
            people.append(person)
        self.people = people
        try:
            self.lid_map = self.store.lid_phone_map()  # V34: refresh lid->phone
        except Exception:  # noqa: BLE001
            self.lid_map = {}

    def load_google(self, people_service) -> int:
        """Load Google Contacts into a standalone directory (NOT merged blindly
        into brain people — name-only overlap is ambiguous and dangerous). Used
        for confident links via phone number (WA jid == phone) and name search.
        """
        self.google_contacts = []
        token = None
        for _ in range(10):  # paginate, cap pages
            resp = (
                people_service.people()
                .connections()
                .list(
                    resourceName="people/me",
                    personFields="names,emailAddresses,phoneNumbers",
                    pageSize=500,
                    pageToken=token,
                )
                .execute()
            )
            for c in resp.get("connections", []):
                name = (c.get("names") or [{}])[0].get("displayName", "")
                emails = {e["value"] for e in c.get("emailAddresses", []) if e.get("value")}
                phones = {p["value"] for p in c.get("phoneNumbers", []) if p.get("value")}
                if not (name and (emails or phones)):
                    continue
                self.google_contacts.append({
                    "name": name,
                    "emails": emails,
                    "phones": phones,
                    "tails": {self._digits(p)[-9:] for p in phones if self._digits(p)},
                })
            token = resp.get("nextPageToken")
            if not token:
                break
        return len(self.google_contacts)

    def link_google(self) -> int:
        """Enrich brain people with real phone/email from Google — but ONLY via an
        UNAMBIGUOUS name match: an alias that maps to exactly ONE Google contact
        (uniqueness in the owner's own address book = confident). This is what
        finds "Kasia" -> "Kasia ❤️" +48… without the old fuzzy-merge wrong-data
        bug (a common given name like "Katarzyna" matches many -> skipped). V33."""
        linked = 0
        for p in self.people:
            best = None
            for alias in sorted(p.aliases, key=len, reverse=True):  # distinctive first
                if len(alias) < 4:
                    continue
                matches = [g for g in self.google_contacts
                           if alias in {t.lower() for t in _TOKEN.findall(g["name"])}]
                if len(matches) == 1:
                    best = matches[0]
                    break
            if best:
                p.emails |= set(best["emails"])
                p.phones |= set(best["phones"])
                linked += 1
        return linked

    def _google_by_tail(self, tail: str) -> dict | None:
        if not tail:
            return None
        for g in self.google_contacts:
            if tail in g["tails"]:
                return g
        return None

    def google_search(self, query: str) -> list[dict]:
        qtok = {t.lower() for t in _TOKEN.findall(query)}
        out = []
        for g in self.google_contacts:
            if qtok & {t.lower() for t in _TOKEN.findall(g["name"])}:
                out.append({"name": g["name"], "emails": sorted(g["emails"]), "phones": sorted(g["phones"])})
        return out[:10]

    # --- live JID linking from WhatsApp PushNames ---
    def _push_links(self) -> dict[str, str]:
        """jid/number -> PushName seen in the store."""
        out: dict[str, str] = {}
        for r in self.store.known_sender_names("whatsapp"):
            if r.get("sender") and r.get("sender_name"):
                out[r["sender"]] = r["sender_name"]
        return out

    def _person_for_pushname(self, pushname: str) -> Person | None:
        ptok = {t.lower() for t in _TOKEN.findall(pushname)}
        for p in self.people:
            if p.aliases & ptok:
                return p
        return None

    def resolve(self, query: str) -> Person | None:
        for p in self.people:
            if p.matches(query):
                return p
        return None

    def jids_for(self, query: str) -> set[str]:
        """All WhatsApp jids/numbers for a person — brain note + linked PushNames."""
        person = self.resolve(query)
        if not person:
            return set()
        jids = set(person.jids)
        # link via PushName match
        for sender, pushname in self._push_links().items():
            if self._person_for_pushname(pushname) is person:
                jids.add(sender)
        # link via phone number: WA jid/sender is the phone number (links old msgs too)
        phone_tails = {self._digits(ph)[-9:] for ph in person.phones if self._digits(ph)}
        if phone_tails:
            for r in self.store.chat_summary("whatsapp"):
                if self._digits(r["chat_id"])[-9:] in phone_tails:
                    jids.add(r["chat_id"])
        return jids

    def name_for(self, sender: str, fallback_pushname: str = "") -> str:
        """Display name for a WA sender id: owner > brain person > google contact >
        pushname > id. Google match is by phone number (WA jid == phone)."""
        if self.is_owner(sender):
            return self.owner_name
        links = self._push_links()
        pushname = fallback_pushname or links.get(sender, "")
        if pushname:
            p = self._person_for_pushname(pushname)
            if p:
                return p.name
        for p in self.people:
            if sender in p.jids:
                return p.name
        g = self._google_by_tail(self._resolved_tail(sender))  # confident: phone match (lid->phone, V34)
        if g:
            return g["name"]
        return pushname or sender

    def enrich_from_google(self, jids: set[str]) -> tuple[set[str], set[str]]:
        """Emails/phones for a set of WA jids, via confident phone match in Google."""
        emails, phones = set(), set()
        for jid in jids:
            g = self._google_by_tail(self._resolved_tail(jid))  # lid->phone->google (V34)
            if g:
                emails |= g["emails"]
                phones |= g["phones"]
        return emails, phones

    # --- autonomous contact write-back (so AIfred maintains ludzie/ itself) ---
    def _unambiguous_person_for_jid(self, sender: str, pushname: str) -> "Person | None":
        """Person a WA sender confidently belongs to, ONLY if unambiguous:
        its PushName alias matches exactly one known person. Avoids wrong writes."""
        ptok = {t.lower() for t in _TOKEN.findall(pushname or "")}
        matched = [p for p in self.people if p.aliases & ptok]
        return matched[0] if len(matched) == 1 else None

    def auto_writeback(self, today: str = "") -> list[dict]:
        """Fill confirmed WhatsApp numbers into ludzie/ notes — but only on an
        UNAMBIGUOUS PushName→alias match, and only where the note still has no
        number (placeholder 'do ustalenia'). Never overwrites real data; never
        guesses. This is what lets the contact book stay current without me."""
        import re as _re
        from datetime import datetime, timezone

        today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # DIRECT chats only — a group JID is not a person's personal number (V24)
        links = {r["sender"]: r["sender_name"]
                 for r in self.store.known_sender_names("whatsapp", direct_only=True)
                 if r.get("sender") and r.get("sender_name")}
        # best unambiguous number per person
        found: dict[str, tuple[str, str]] = {}  # slug -> (number, pushname)
        for sender, pushname in links.items():
            num = self._digits(sender)
            if not num or self.is_owner(sender):
                continue
            person = self._unambiguous_person_for_jid(sender, pushname)
            if person is not None and person.slug not in found:
                found[person.slug] = (sender, pushname)
        changes: list[dict] = []
        for p in self.people:
            if p.slug not in found or not p.path:
                continue
            number, pushname = found[p.slug]
            try:
                content = self.brain.read(p.path)
            except Exception:  # noqa: BLE001
                continue
            if number in content or self._has_wa_number(content):  # already recorded / has one
                continue
            new = self._inject_number(content, number, pushname, today)
            if new == content:
                continue
            try:
                self.brain.write(p.path, new)
            except Exception:  # noqa: BLE001
                continue
            p.phones.add(number)
            changes.append({"person": p.name, "slug": p.slug, "number": number})
        return changes

    @staticmethod
    def _has_wa_number(content: str) -> bool:
        """A WhatsApp/Telefon line that already carries a real number (>=9 digits)."""
        return bool(re.search(r"(WhatsApp|Telefon|Tel)\b[^\n]*\d{9,}", content, re.IGNORECASE))

    @staticmethod
    def _inject_number(content: str, number: str, pushname: str, today: str) -> str:
        line = f"- WhatsApp: {number} (PushName: {pushname}) — potwierdzone {today} (auto)"
        placeholder = re.compile(
            r"^[ \t]*[-*]?\s*\**\s*(WhatsApp|Kontakt)\b.*?(do ustalenia|pojawi się automatycznie|numer pojawi).*$",
            re.IGNORECASE | re.MULTILINE,
        )
        if placeholder.search(content):
            return placeholder.sub(line, content, count=1)
        # no placeholder: insert under a "## Kontakt" heading if present
        m = re.search(r"^##\s*Kontakt.*$", content, re.MULTILINE)
        if m:
            i = m.end()
            return content[:i] + "\n" + line + content[i:]
        return content  # no safe place to put it -> leave untouched

    # --- owner-addressing terms (per-person pet names for the owner) ---
    def owner_terms_for(self, sender: str, person_name: str = "") -> set[str]:
        """Terms by which THIS sender/person calls the owner. Scoped per person so
        a generic word like 'kotek' only counts when the right person says it."""
        p = None
        if person_name:
            p = self.resolve(person_name)
        if p is None:
            for cand in self.people:
                if sender in cand.jids or self._digits(sender)[-9:] in {self._digits(j)[-9:] for j in cand.jids if self._digits(j)}:
                    p = cand
                    break
        if p is None:
            pushname = self._push_links().get(sender, "")
            p = self._person_for_pushname(pushname) if pushname else None
        return set(p.owner_terms) if p else set()

    def all_owner_terms(self) -> set[str]:
        out: set[str] = set()
        for p in self.people:
            out |= p.owner_terms
        return out

    def teach_owner_term(self, person_query: str, term: str) -> dict:
        """Record that `person_query` calls the owner `term` — persisted to their
        brain.md note so it survives restarts and the owner can see/edit it."""
        p = self.resolve(person_query)
        if p is None or not p.path:
            return {"ok": False, "reason": f"nieznana osoba: {person_query}"}
        term = term.strip().lower()
        if not term:
            return {"ok": False, "reason": "pusty termin"}
        p.owner_terms.add(term)
        try:
            content = self.brain.read(p.path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": str(e)}
        new = self._inject_owner_terms(content, sorted(p.owner_terms))
        try:
            self.brain.write(p.path, new)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": str(e)}
        return {"ok": True, "person": p.name, "terms": sorted(p.owner_terms)}

    @staticmethod
    def _inject_owner_terms(content: str, terms: list[str]) -> str:
        line = f"- **Mówi do mnie:** {', '.join(terms)}"
        existing = re.compile(r"^[ \t]*[-*]?\s*\**\s*Mówi do mnie\b.*$", re.IGNORECASE | re.MULTILINE)
        if existing.search(content):
            return existing.sub(line, content, count=1)
        m = re.search(r"^##\s*Podstawowe.*$", content, re.MULTILINE)
        if m:
            i = m.end()
            return content[:i] + "\n" + line + content[i:]
        return content.rstrip() + "\n\n" + line + "\n"

    def name_for_email(self, email: str) -> str | None:
        el = (email or "").lower()
        if not el:
            return None
        for p in self.people:
            if any(el in e.lower() for e in p.emails):
                return p.name
        for g in self.google_contacts:
            if any(el in e.lower() for e in g["emails"]):
                return g["name"]
        return None

    def is_known_person(self, query: str) -> bool:
        """True if query (name/email) maps to a brain person (for triage weight)."""
        return self.resolve(query) is not None

    def describe(self, query: str) -> dict:
        p = self.resolve(query)
        if not p:
            # not in brain.md — maybe a Google contact
            g = self.google_search(query)
            if g:
                return {"found": True, "source": "google", "candidates": g}
            return {"found": False, "query": query}
        jids = self.jids_for(query)
        emails = set(p.emails)
        phones = set(p.phones)
        ge, gp = self.enrich_from_google(jids)  # confident: only via linked WA number
        emails |= ge
        phones |= gp
        out = {
            "found": True,
            "source": "brain",
            "name": p.name,
            "aliases": sorted(p.aliases),
            "role": p.role,
            "whatsapp": sorted(jids),
            "emails": sorted(emails),
            "phones": sorted(phones),
        }
        # no confident identifiers yet -> offer Google name matches as candidates (don't assign)
        if not jids and not emails and not phones:
            cand = self.google_search(p.name) or self.google_search(" ".join(p.aliases))
            if cand:
                out["google_candidates"] = cand
                out["note"] = "no confirmed contact yet; candidates from Google by name — confirm which is correct"
        return out
