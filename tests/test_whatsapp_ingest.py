"""T9 whatsapp ingest tests (V5 single-instance, V13 incremental/dedup)."""

import pytest

from aifred.store.db import Store
from aifred.whatsapp.ingest import (
    SingleInstanceLock,
    WhatsAppLockError,
    ingest_batch,
    normalize,
)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_single_instance_lock(tmp_path):
    lock = tmp_path / "wa.lock"
    a = SingleInstanceLock(lock)
    a.acquire()
    b = SingleInstanceLock(lock)
    with pytest.raises(WhatsAppLockError):  # V5
        b.acquire()
    a.release()
    b.acquire()  # free after release
    b.release()


def test_normalize_baileys_shape():
    m = normalize(
        {"key": {"id": "B1", "remoteJid": "fam@g.us"}, "messageTimestamp": 100, "message": {"conversation": "hi"}}
    )
    assert m.ext_id == "B1" and m.chat_id == "fam@g.us" and m.body == "hi" and m.ts == 100


def test_normalize_whatsmeow_shape():
    m = normalize({"id": "W1", "chat": "fam", "sender": "mum", "ts": 5, "body": "dinner?"})
    assert m.ext_id == "W1" and m.sender == "mum" and m.body == "dinner?"


def test_normalize_requires_id():
    with pytest.raises(ValueError):  # V13 dedup needs id
        normalize({"body": "x"})


def test_ingest_dedups_and_cursor(store):
    msgs = [normalize({"id": "1", "chat": "fam", "ts": 1, "body": "a"}),
            normalize({"id": "2", "chat": "fam", "ts": 2, "body": "b"})]
    assert ingest_batch(store, msgs) == 2
    assert ingest_batch(store, msgs) == 0  # V13 idempotent
    fresh = store.new_messages("whatsapp", "fam")
    assert [r["body"] for r in fresh] == ["a", "b"]
    assert store.new_messages("whatsapp", "fam") == []  # cursor advanced
