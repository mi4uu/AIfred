"""Proactive triage engine — periodically decide what needs Owner's attention.

Pulls only NEW mail + WhatsApp (cursors), resolves senders to known people
(weight), and classifies importance in SMALL batches so nothing overflows the
context or gets hallucinated (V11/C7/V15):
  collect -> resolve -> prefilter (code) -> classify (batched LLM) -> aggregate

Outputs: attention items in the store (deduped by source msg id), a daily
brain.md note, and a list of high-importance items to push to Telegram.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from aifred.store.db import Store
from aifred.whatsapp.worker import derive_is_group

log = logging.getLogger("aifred.triage")

BATCH = 6           # items per LLM classification call (small = bounded ctx)
SNIPPET_MAX = 180   # chars of body/snippet shown to the model
CTX_LINES = 2        # preceding thread lines for a DIRECT chat (V19)
GROUP_CTX_LINES = 6  # more for GROUPS — addressee is inferred from the flow (V38)
MAIL_WINDOW = "newer_than:2d"
NOISE = ("no-reply", "noreply", "no_reply", "notifications@", "newsletter", "mailer-daemon", "donotreply")
_EMAIL = re.compile(r"<([^>]+)>")


def _norm_self_ts(ts: float) -> float:
    """WhatsApp stores ms; normalize to epoch seconds."""
    return ts / 1000.0 if ts > 1e12 else ts

# Deterministic guard patterns (code-first, V15) — small model gets these wrong.
# One-time codes / 2FA: noise, never an owner action even though they look urgent.
_OTP = re.compile(
    r"\b(otp|one[- ]time|verification code|login code|kod (logowania|weryfikacyjny|do logowania)|"
    r"hasło jednorazowe|jednorazowy kod|security code|2fa|kod sms)\b",
    re.IGNORECASE,
)
# Automated self/report mail that slips past the NOISE sender list.
_AUTOMATED = re.compile(r"\b(daily report|raport dzienny|status report|cron|backup (ok|complete)|build (passed|failed))\b", re.IGNORECASE)
# Phishing-shaped payment/verification mail — looks urgent, almost always a scam.
_PHISH = re.compile(
    r"(payment (notification|requiring|confirm)|requir\w* verification|verify your|"
    r"unusual activity|account (suspend|limit|lock)|zweryfikuj|potwierdź (płatność|konto|dane)|"
    r"weryfikacj\w* (płatnoś|konta)|twoje konto zostanie|kliknij (tutaj|w link))",
    re.IGNORECASE,
)


@dataclass
class TriageItem:
    source: str          # 'mail' | 'whatsapp'
    ext_id: str
    sender: str
    person: str | None
    ts: float
    subject: str
    snippet: str
    is_group: bool = False
    chat_id: str = ""
    context: str = ""           # preceding thread lines, for the classifier (V19)
    importance: str = "low"
    needs_action: bool = False
    directed_at_me: bool = True
    forced: bool = False        # set by a learned rule -> skip the skeptical re-check
    review: bool = False        # model unsure -> route to UI "Do decyzji" (V21)
    suggest: str = ""           # model's own call, shown as the suggestion in review
    is_self_note: bool = False  # owner's note/forward in his self-chat -> capture (V26)
    why: str = ""

    def label(self) -> str:
        who = self.person or self.sender
        ctx = " (grupa)" if self.is_group else ""
        head = self.subject or self.snippet[:60]
        tail = f" — {self.why}" if self.why else ""
        return f"[{self.source}{ctx}] {who}: {head}{tail}"


def _classify_prompt(owner: list[str]) -> str:
    who = "/".join(owner)
    return (
        f"You triage messages for {who} (the owner). For EACH item return importance "
        "(high/medium/low), needs_action (bool), directed_at_me (bool). "
        f"directed_at_me = is this addressed to {who} specifically or does it ask HIM to act? "
        "Use the conversation context shown as 'earlier: …' to find the ADDRESSEE — don't rely "
        "only on names. In a GROUP a message can be for the owner WITHOUT naming him: if the owner "
        "(shown as 'JA') asked/said something and THIS line answers or follows up on it, "
        "directed_at_me=true. A message clearly aimed at someone else (answers another person, or "
        "starts with another name) is NOT directed_at_me -> importance low. high only if it clearly "
        "needs the owner's action soon (request to him, deadline, payment, appointment). "
        "Automated/newsletter/code/OTP = low. Reply ONLY JSON array: "
        '[{"i":0,"importance":"high","needs_action":true,"directed_at_me":true,"why":"short"}]'
    )


class TriageEngine:
    def __init__(self, store: Store, llm, contacts=None, gmail_service=None, owner_aliases: str = "",
                 owner_email: str = "", owner_lid: str = "", owner_phone: str = "", brain=None):
        self.store = store
        self.llm = llm
        self.contacts = contacts
        self.gmail = gmail_service
        self.brain = brain
        self.on_self_notes = None  # optional callback(text, batch_ref)->int — propose events (V27)
        self.owner = [a.strip() for a in (owner_aliases or "").split(",") if a.strip()] or ["Owner"]
        self.owner_email = (owner_email or "").strip().lower()
        self.owner_lid = re.sub(r"\D", "", owner_lid or "")  # owner's own WA @lid -> skip own msgs (V22)
        # owner's self-chat (message-yourself) JIDs — these notes are KEPT (V26)
        self.owner_ids = {x for x in (self.owner_lid, re.sub(r"\D", "", owner_phone or "")) if x}

    def _is_self_chat(self, chat_id: str) -> bool:
        d = re.sub(r"\D", "", chat_id or "")
        if not d:
            return False
        if d in self.owner_ids or (len(d) >= 9 and d[-9:] in {o[-9:] for o in self.owner_ids if len(o) >= 9}):
            return True
        return bool(self.contacts and self.contacts.is_owner(chat_id))

    # ---- collect (incremental) ----
    def collect_mail(self, limit: int = 20) -> list[TriageItem]:
        if self.gmail is None:
            return []
        from aifred.google.tools import gmail_search

        since = float(self.store.get_setting("triage_mail_ts", "0"))
        items: list[TriageItem] = []
        newest = since
        for h in gmail_search(self.gmail, MAIL_WINDOW, max_results=limit):
            ts = float(h.get("internal_ts", 0))
            if ts <= since:
                continue
            newest = max(newest, ts)
            frm = h.get("from", "")
            em = _EMAIL.search(frm)
            email = (em.group(1) if em else frm).strip().lower()
            person = self.contacts.name_for_email(email) if self.contacts else None
            items.append(TriageItem("mail", h["id"], email, person, ts, h.get("subject", ""), h.get("snippet", "")))
        if newest > since:
            self.store.set_setting("triage_mail_ts", str(newest))
        return items

    def collect_whatsapp(self, limit: int = 50) -> list[TriageItem]:
        last_id = int(self.store.get_setting("triage_wa_id", "0"))
        rows = self.store.messages_after_id(last_id, "whatsapp", limit)
        items: list[TriageItem] = []
        newest = last_id
        for r in rows:
            newest = max(newest, r["id"])
            sender = r["sender"]
            chat_id = r["chat_id"]
            own = (("from_me" in r.keys() and r["from_me"]) or
                   (self.owner_lid and re.sub(r"\D", "", sender or "") == self.owner_lid))
            self_chat = self._is_self_chat(chat_id)
            # own message in someone ELSE's chat = your side of a convo -> skip (V22).
            # own message in your SELF-chat = a deliberate note/forward -> KEEP (V26).
            if own and not self_chat:
                continue
            if self_chat:
                # owner's own inbox — a note or forwarded message to himself
                items.append(TriageItem(
                    "whatsapp", r["ext_id"], sender, "📝 Notatka (WA do siebie)", r["ts"], "",
                    r["body"] or "", chat_id=chat_id, is_self_note=True,
                ))
                continue
            person = None
            if self.contacts:
                nm = self.contacts.name_for(sender, r["sender_name"] or "")
                person = nm if nm != sender else (r["sender_name"] or None)
            # Re-derive group flag — never trust the stored bit (B3: old/backfilled
            # rows have it wrong, which leaked group chatter as 'high').
            is_group = bool(r["is_group"]) or derive_is_group(chat_id, sender)
            items.append(TriageItem(
                "whatsapp", r["ext_id"], sender, person, r["ts"], "", r["body"] or "",
                is_group=is_group, chat_id=chat_id,
                context=self._thread_context("whatsapp", chat_id, r["ts"], is_group),
            ))
        if newest > last_id:
            self.store.set_setting("triage_wa_id", str(newest))
        return items

    def _thread_context(self, channel: str, chat_id: str, before_ts: float, is_group: bool = False) -> str:
        """Preceding thread lines so the model can infer the addressee from the
        flow (V19/V38). Groups get more lines and the owner's own lines are marked
        'JA' so a reply to the owner's question is recognisable as directed to him."""
        if not chat_id:
            return ""
        n = GROUP_CTX_LINES if is_group else CTX_LINES
        rows = self.store.recent_messages(channel, limit=n + 6, chat_id=chat_id)
        prior = [r for r in rows if r["ts"] < before_ts and (r["body"] or "").strip()][-n:]
        out = []
        for r in prior:
            if self.contacts and self.contacts.is_owner(r["sender"]):
                who = "JA (właściciel)"
            elif self.contacts:
                who = self.contacts.name_for(r["sender"], r["sender_name"] or "")
            else:
                who = r["sender_name"] or r["sender"]
            out.append(f"{who}: {(r['body'] or '').strip()[:80]}")
        return " | ".join(out)

    # ---- prefilter (pure code, V15) ----
    def prefilter(self, items: list[TriageItem]) -> list[TriageItem]:
        kept = []
        for it in items:
            known = bool(it.person) or (self.contacts and self.contacts.is_known_person(it.sender))
            noisy = any(n in (it.sender or "").lower() for n in NOISE)
            if noisy and not known:
                continue  # drop automated noise unless from someone we know
            if it.source == "whatsapp" and not (it.snippet or "").strip():
                continue  # skip empty/media-only WA
            kept.append(it)
        return kept

    # ---- classify (batched LLM, bounded) ----
    def classify(self, items: list[TriageItem]) -> list[TriageItem]:
        for start in range(0, len(items), BATCH):
            batch = items[start : start + BATCH]
            lines = []
            for i, it in enumerate(batch):
                who = it.person or it.sender
                where = "GROUP" if it.is_group else "direct"
                body = (it.subject + " " + it.snippet).strip()[:SNIPPET_MAX]
                ctx = f" (earlier: {it.context})" if it.context else ""
                lines.append(f'{i}. [{where}] from {who}: {body}{ctx}')
            msgs = [
                {"role": "system", "content": _classify_prompt(self.owner)},
                {"role": "user", "content": "\n".join(lines)},
            ]
            try:
                data = json.loads(self.llm.chat(msgs).content)
            except (json.JSONDecodeError, AttributeError, TypeError):
                data = []
            by_i = {d.get("i"): d for d in data if isinstance(d, dict)}
            for i, it in enumerate(batch):
                d = by_i.get(i, {})
                it.importance = str(d.get("importance", "low")).lower()
                it.needs_action = bool(d.get("needs_action", False))
                it.directed_at_me = bool(d.get("directed_at_me", not it.is_group))
                it.why = str(d.get("why", ""))[:120]
                self._guard(it)
                # this person's pet name for the owner (e.g. Kasia says "kotek") =>
                # the message IS addressed to the owner, even if the model missed it
                if self._addresses_owner(it):
                    it.directed_at_me = True
                    if it.importance == "low":
                        it.importance = "medium"
                # group chatter not addressed to the owner -> never high/medium
                if it.is_group and not it.directed_at_me:
                    it.importance = "low"
                    it.needs_action = False
                # known person boost ONLY in direct chats (not group noise / not automated)
                elif it.person and not it.is_group and it.importance == "low" and not self._is_automated(it):
                    it.importance = "medium"
        return items

    def _addresses_owner(self, it: TriageItem) -> bool:
        """True if the sender used one of THEIR known pet names for the owner (V25)."""
        if not self.contacts:
            return False
        terms = self.contacts.owner_terms_for(it.sender, it.person or "")
        if not terms:
            return False
        body = f"{it.subject} {it.snippet}".lower()
        return any(re.search(rf"\b{re.escape(t)}", body) for t in terms)

    # ---- deterministic guards (code-first overrides, V15) ----
    def _is_automated(self, it: TriageItem) -> bool:
        text = f"{it.subject} {it.snippet}"
        if _AUTOMATED.search(text):
            return True
        if self.owner_email and it.sender.lower() == self.owner_email:
            return True  # mail from your own address = automated report, not a real person
        return False

    def _guard(self, it: TriageItem) -> None:
        """Fix the classes the small model reliably mis-rates (audited from real data):
        OTP/login codes look urgent but are noise; automated self-reports aren't
        a person asking. Force these low regardless of the model."""
        text = f"{it.subject} {it.snippet}"
        known = bool(it.person) or (self.contacts and self.contacts.is_known_person(it.sender))
        if _OTP.search(text):
            it.importance, it.needs_action, it.directed_at_me = "low", False, False
            if not it.why:
                it.why = "kod jednorazowy/OTP — nie wymaga działania"
        elif _PHISH.search(text) and not known:
            # urgent-looking payment/verification mail from a stranger = scam, not action
            it.importance, it.needs_action, it.directed_at_me = "low", False, False
            if not it.why:
                it.why = "podejrzane (phishing/weryfikacja płatności)"
        elif self._is_automated(it):
            it.importance, it.needs_action = "low", False
            if not it.why:
                it.why = "raport automatyczny"

    # ---- learned rules (deterministic override, V15) ----
    def apply_rules(self, items: list[TriageItem]) -> list[TriageItem]:
        rules = self.store.list_rules()
        if not rules:
            return items
        for it in items:
            who = (it.person or "").lower()
            sender = (it.sender or "").lower()
            domain = sender.split("@")[-1] if "@" in sender else ""
            body = (it.subject + " " + it.snippet).lower()
            for r in rules:
                scope, pat, action = r["scope"], r["pattern"], r["action"]
                hit = (
                    (scope == "sender" and (pat in who or pat in sender))
                    or (scope == "group" and pat in sender)
                    or (scope == "domain" and domain and pat in domain)
                    or (scope == "category" and pat in body)
                )
                if not hit:
                    continue
                if action == "mute":
                    it.importance, it.needs_action, it.directed_at_me = "low", False, False
                elif action == "vip":
                    it.importance, it.directed_at_me, it.forced = "high", True, True
                elif action in ("high", "medium", "low"):
                    it.importance, it.forced = action, True
        return items

    # ---- second opinion on push candidates (V20) ----
    def verify_push(self, items: list[TriageItem]) -> list[TriageItem]:
        """Re-check items about to be pushed (high + directed) one-by-one with the
        full thread context and a skeptical prompt. Small model over-fires 'high';
        a disagreeing second pass downgrades to medium so it shows in the UI but
        does NOT ping Telegram. Cheap: only the few high candidates are re-judged."""
        for it in items:
            if it.forced or not (it.importance == "high" and it.directed_at_me and not it.is_group):
                continue
            who = it.person or it.sender
            body = (it.subject + " " + it.snippet).strip()[:SNIPPET_MAX]
            ctx = f"\nEarlier in thread: {it.context}" if it.context else ""
            msgs = [
                {"role": "system", "content": (
                    f"You double-check whether ONE message truly needs {'/'.join(self.owner)}'s action "
                    "NOW (a direct request to him, a deadline, payment, appointment). Be skeptical: "
                    "social chit-chat, FYI, thanks, codes, automated mail are NOT high. "
                    'Reply ONLY JSON: {"high":true|false,"why":"short"}'
                )},
                {"role": "user", "content": f"From {who}: {body}{ctx}"},
            ]
            try:
                d = json.loads(self.llm.chat(msgs).content)
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue  # parse fail -> trust first pass
            if isinstance(d, dict) and d.get("high") is False:
                # model and skeptic disagree -> genuinely uncertain. Don't push;
                # route to the UI review queue so the owner decides and teaches.
                it.suggest = "high"
                it.importance = "medium"
                it.review = True
                if d.get("why"):
                    it.why = str(d["why"])[:120]
        return items

    def _meta(self, it: TriageItem) -> str:
        """Rule-target sidecar for a review item — lets a one-click decision
        build a scoped rule (V21)."""
        sender = (it.sender or "").lower()
        domain = sender.split("@")[-1] if "@" in sender else ""
        return json.dumps({
            "source": it.source,
            "sender": sender,
            "person": it.person or "",
            "domain": domain,
            "is_group": it.is_group,
            "suggest": it.suggest or it.importance,
            "why": it.why,
        })

    # ---- self-notes (owner's message-yourself inbox, V26) ----
    def handle_self_notes(self, items: list[TriageItem]) -> int:
        """Capture the owner's notes/forwards to himself: persist to brain.md and
        surface as a medium attention item. These are deliberate, never noise.
        If an on_self_notes callback is set, hand the captured text to it (the
        service turns it into confirm-over-Telegram calendar proposals, V27)."""
        captured: list[TriageItem] = []
        for it in items:
            it.importance, it.directed_at_me, it.why = "medium", True, "notatka/przesłane do siebie"
            ref = f"selfnote:{it.ext_id}"
            if not self.store.add_attention("triage:selfnote", "medium", it.label(), ref, it.ts):
                continue  # already captured
            captured.append(it)
            if self.brain is not None and (it.snippet or "").strip():
                from datetime import datetime, timezone

                stamp = datetime.fromtimestamp(_norm_self_ts(it.ts), timezone.utc).strftime("%Y-%m-%d %H:%M")
                try:
                    self.brain.append(f"- {stamp} (WA→ja) {it.snippet.strip()}", path="Journal/inbox.md")
                except Exception as e:  # noqa: BLE001
                    log.warning("self-note brain append failed: %s", e)
        if captured and self.on_self_notes:
            text = "\n".join((it.snippet or "").strip() for it in captured if (it.snippet or "").strip())
            batch_ref = f"selfnote-batch:{max(it.ext_id for it in captured)}"
            try:
                self.on_self_notes(text, batch_ref)
            except Exception as e:  # noqa: BLE001
                log.warning("self-note proposer failed: %s", e)
        return len(captured)

    # ---- re-judge stored open items (V37): guards + identity change after the fact ----
    def rejudge_open(self) -> int:
        """Stored attention items are frozen at triage time. Re-apply the phishing/
        OTP guards (so a later-added guard demotes an old false-high) and re-resolve
        the sender's name (so lid->phone/Google upgrades fix a stale label like
        'Tomek' -> 'Tomasz Nowak'). Persists changes; idempotent."""
        changed = 0
        for it in self.store.list_attention("open"):
            content, kind, ref, iid = it["content"], it["kind"], it["ref"], it["id"]
            # 1) re-resolve the sender name from the original message (identity may have
            #    improved). Rewrite ONLY the name segment "[prefix] <name>: ..." so a
            #    substring overlap can't corrupt it (and stale/double names get repaired).
            new_content = content
            if self.contacts and ref.startswith("whatsapp:"):
                msg = self.store.message_by_ext(ref.split(":", 1)[1])
                if msg:
                    new = self.contacts.name_for(msg["sender"], msg["sender_name"] or "")
                    m = re.match(r"^(\[[^\]]*\] ).*?(: .*)$", content, re.DOTALL)
                    if new and m:
                        rebuilt = f"{m.group(1)}{new}{m.group(2)}"
                        if rebuilt != content:
                            new_content = rebuilt
            if new_content != content:
                self.store.set_item_content(iid, new_content)
                changed += 1
            # 2) re-apply deterministic guards (phishing/OTP) to demote frozen false-highs
            if kind != "low" and (_PHISH.search(new_content) or _OTP.search(new_content)):
                self.store.set_item_kind(iid, "low")
                changed += 1
        return changed

    # ---- aggregate + persist ----
    def aggregate(self, items: list[TriageItem]) -> dict:
        high = []
        added = 0
        review = 0
        for it in items:
            ref = f"{it.source}:{it.ext_id}"
            status = "review" if it.review else "open"
            meta = self._meta(it) if it.review else ""
            if self.store.add_attention(f"triage:{it.source}", it.importance, it.label(), ref, it.ts, status, meta):
                added += 1
                if it.review:
                    review += 1
                elif it.importance == "high" and it.directed_at_me:  # push only what's for the owner
                    high.append(it.label())
        return {"new": added, "high": high, "review": review, "scanned": len(items)}

    def run(self) -> dict:
        items = self.collect_mail() + self.collect_whatsapp()
        if not items:
            return {"new": 0, "high": [], "review": 0, "notes": 0, "scanned": 0}
        self_notes = [it for it in items if it.is_self_note]
        rest = [it for it in items if not it.is_self_note]
        rest = self.prefilter(rest)
        rest = self.classify(rest)
        rest = self.apply_rules(rest)  # learned overrides (mute/vip) win over LLM
        rest = self.verify_push(rest)  # skeptical second opinion before Telegram
        notes = self.handle_self_notes(self_notes)  # capture owner's own inbox (V26)
        result = self.aggregate(rest)
        result["notes"] = notes
        log.info("triage: scanned=%s new=%s high=%s notes=%s",
                 result["scanned"] + len(self_notes), result["new"], len(result["high"]), notes)
        return result
