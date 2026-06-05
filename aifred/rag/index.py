"""In-engine semantic memory (V31).

Indexes AIfred's OWN operational data — chat turns, WhatsApp (across days), mail
snippets/attention, self-notes, journal inbox — into the embeddings table, then
recall(query, k) returns the few most relevant snippets across sources/days.

This is the "retrieve, don't dump" lever the eval pointed at: instead of feeding
a big raw blob (which collapses the model at ~10k tokens), the agent pulls a
handful of focused lines. Incremental: each snippet embedded once (dedup by ref).
"""

from __future__ import annotations

import logging

from aifred.rag.embedder import Embedder, cosine, pack, unpack

log = logging.getLogger("aifred.rag")

BATCH = 32
MIN_LEN = 4         # skip trivially short snippets
SNIPPET_MAX = 240   # chars stored/embedded per snippet
MUTABLE = {"inbox"}  # sources that can be edited in brain.md -> prune stale refs (V32)


def _clip(s: str) -> str:
    return " ".join((s or "").split())[:SNIPPET_MAX]


class RagIndex:
    def __init__(self, store, embedder: Embedder, contacts=None, brain=None):
        self.store = store
        self.emb = embedder
        self.contacts = contacts
        self.brain = brain

    # ---- gather indexable snippets from AIfred's own data ----
    def _candidates(self) -> list[tuple[str, str, str, float]]:
        """(ref, source, text, ts) for everything worth remembering."""
        out: list[tuple[str, str, str, float]] = []
        # whatsapp — resolve sender to a name; skip owner's own + empty
        for r in self.store.recent_messages("whatsapp", limit=2000):
            body = (r["body"] or "").strip()
            if len(body) < MIN_LEN:
                continue
            if self.contacts and self.contacts.is_owner(r["sender"]):
                continue
            who = r["sender_name"] or r["sender"]
            if self.contacts:
                who = self.contacts.name_for(r["sender"], r["sender_name"] or "")
            out.append((f"wa:{r['ext_id']}", "whatsapp", _clip(f"{who}: {body}"), float(r["ts"])))
        # triage attention / self-notes — already curated as important
        for status in ("open", "done"):
            for it in self.store.list_attention(status):
                out.append((f"att:{it['id']}", "attention", _clip(it["content"]), float(it["created_ts"])))
        # brain.md inbox — the owner's self-notes/forwards (high-value memory)
        if self.brain is not None:
            try:
                inbox = self.brain.read("Journal/inbox.md")
            except Exception:  # noqa: BLE001
                inbox = ""
            for i, line in enumerate(inbox.splitlines()):
                ln = line.lstrip("-* ").strip()
                if len(ln) >= MIN_LEN and "WA→ja" in line:
                    body = ln.split("(WA→ja)", 1)[-1].strip()
                    out.append((f"inbox:{hash(ln) & 0xffffffff}", "inbox", _clip(body), 0.0))
        # chat turns (what the owner asked / was told)
        for s in self.store.list_sessions():
            for m in self.store.session_messages(s["id"]):
                body = (m["content"] or "").strip()
                if len(body) >= MIN_LEN:
                    out.append((f"chat:{s['id']}:{m.get('ts', 0)}:{hash(body) & 0xffffff}",
                                "chat", _clip(f"{m['role']}: {body}"), float(m.get("ts", 0) or 0)))
        return out

    # ---- incremental embed + prune (re-sync with brain.md edits, V32) ----
    def refresh(self) -> int:
        cands = self._candidates()
        # prune mutable sources (e.g. inbox) so edited/deleted lines drop their vectors
        by_source: dict[str, set[str]] = {}
        for ref, source, _, _ in cands:
            by_source.setdefault(source, set()).add(ref)
        for source in MUTABLE:
            self.store.prune_embeddings(source, by_source.get(source, set()))
        have = self.store.embedded_refs()
        todo = [c for c in cands if c[0] not in have]
        if not todo:
            return 0
        n = 0
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            try:
                vecs = self.emb.embed([c[2] for c in chunk])
            except Exception as e:  # noqa: BLE001
                log.warning("embed batch failed: %s", e)
                break
            for (ref, source, text, ts), vec in zip(chunk, vecs):
                if vec:
                    self.store.add_embedding(ref, source, text, pack(vec), ts)
                    n += 1
        if n:
            log.info("rag: embedded %s new snippets", n)
        return n

    # ---- recall ----
    def recall(self, query: str, k: int = 5, sources: list[str] | None = None, min_score: float = 0.3) -> list[dict]:
        qv = self.emb.embed_one(query)
        if not qv:
            return []
        rows = self.store.all_embeddings()
        scored = []
        for r in rows:
            if sources and r["source"] not in sources:
                continue
            sc = cosine(qv, unpack(r["vec"]))
            if sc >= min_score:
                scored.append((sc, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"text": r["text"], "source": r["source"], "score": round(sc, 3), "ts": r["ts"]}
                for sc, r in scored[:k]]
