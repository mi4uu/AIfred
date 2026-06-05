"""In-engine RAG (V31) — pack/cosine + incremental index + recall, fake embedder."""

from aifred.rag.embedder import cosine, pack, unpack
from aifred.rag.index import RagIndex
from aifred.store.db import Store


def test_pack_roundtrip_and_cosine():
    v = [0.1, 0.2, 0.3, 0.4]
    assert list(round(x, 4) for x in unpack(pack(v))) == v
    assert round(cosine(v, v), 5) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0


class FakeEmbedder:
    """Deterministic toy embedding: bag-of-keywords over a fixed vocab."""
    VOCAB = ["mleko", "paczka", "dentysta", "zosia", "praca", "spotkanie"]

    def embed(self, texts):
        return [self._vec(t) for t in texts]

    def embed_one(self, text):
        return self._vec(text)

    def _vec(self, text):
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in self.VOCAB]


class FakeContacts:
    def is_owner(self, sender):
        return sender == "OWNER"

    def name_for(self, sender, push=""):
        return push or sender


def _seed(s):
    s.add_message("whatsapp", "c1", "w1", ts=1.0, body="kup mleko i odbierz paczkę", sender="48500", sender_name="Kasia")
    s.add_message("whatsapp", "c1", "w2", ts=2.0, body="dentysta Zosia jutro", sender="48500", sender_name="Kasia")
    s.add_message("whatsapp", "c1", "w3", ts=3.0, body="ja sam do siebie", sender="OWNER", sender_name="Sówka", from_me=True)
    s.add_message("whatsapp", "c1", "w4", ts=4.0, body="x", sender="48500", sender_name="Kasia")  # too short


def test_refresh_indexes_incrementally_and_skips_owner():
    s = Store(":memory:")
    _seed(s)
    idx = RagIndex(s, FakeEmbedder(), contacts=FakeContacts())
    n = idx.refresh()
    assert n == 2                       # w1, w2 indexed; owner (w3) + short (w4) skipped
    assert idx.refresh() == 0           # incremental: nothing new second time
    s.close()


def test_recall_ranks_by_similarity():
    s = Store(":memory:")
    _seed(s)
    idx = RagIndex(s, FakeEmbedder(), contacts=FakeContacts())
    idx.refresh()
    hits = idx.recall("kiedy paczka i mleko", k=3)
    assert hits and "mleko" in hits[0]["text"]          # most similar first
    assert all(h["source"] == "whatsapp" for h in hits)
    # a query about something not indexed -> filtered by min_score
    assert idx.recall("zupełnie inny temat xyz", k=3, min_score=0.5) == []
    s.close()


class FakeBrain:
    def __init__(self, inbox):
        self.inbox = inbox

    def read(self, path):
        return self.inbox if path == "Journal/inbox.md" else ""


def test_inbox_edit_prunes_stale_vectors():
    s = Store(":memory:")
    brain = FakeBrain("- 2026-06-04 09:00 (WA→ja) dentysta Zosia 16:40\n"
                       "- 2026-06-04 09:05 (WA→ja) kup truskawki")
    idx = RagIndex(s, FakeEmbedder(), contacts=FakeContacts(), brain=brain)
    assert idx.refresh() == 2
    inbox_rows = [r for r in s.all_embeddings() if r["source"] == "inbox"]
    assert len(inbox_rows) == 2
    # owner edits the inbox in brain.md: one line changed, one removed
    brain.inbox = "- 2026-06-04 09:00 (WA→ja) dentysta Zosia PRZENIESIONA 18:00"
    idx.refresh()
    inbox_rows = [r for r in s.all_embeddings() if r["source"] == "inbox"]
    assert len(inbox_rows) == 1                       # stale lines pruned
    assert "PRZENIESIONA" in inbox_rows[0]["text"]    # edited content re-indexed
    s.close()


def test_recall_source_filter():
    s = Store(":memory:")
    _seed(s)
    idx = RagIndex(s, FakeEmbedder(), contacts=FakeContacts())
    idx.refresh()
    assert idx.recall("mleko", k=5, sources=["attention"]) == []  # none in that source
    assert idx.recall("mleko", k=5, sources=["whatsapp"])         # present
    s.close()
