"""T5 store tests (I.store, V13 incremental + dedup)."""

import pytest

from aifred.store.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_add_message_dedups(store):
    assert store.add_message("whatsapp", "fam", "m1", ts=1.0, body="hi") is True
    assert store.add_message("whatsapp", "fam", "m1", ts=1.0, body="hi") is False  # dup ignored


def test_cursor_incremental(store):
    store.add_message("whatsapp", "fam", "m1", ts=1.0, body="a")
    store.add_message("whatsapp", "fam", "m2", ts=2.0, body="b")
    first = store.new_messages("whatsapp", "fam")
    assert [r["body"] for r in first] == ["a", "b"]
    # V13: second call returns nothing new (cursor advanced)
    assert store.new_messages("whatsapp", "fam") == []
    store.add_message("whatsapp", "fam", "m3", ts=3.0, body="c")
    third = store.new_messages("whatsapp", "fam")
    assert [r["body"] for r in third] == ["c"]


def test_items_cache(store):
    iid = store.add_item("whatsapp:fam", "todo", "buy milk", created_ts=1.0)
    assert [r["content"] for r in store.list_items("open")] == ["buy milk"]
    store.set_item_status(iid, "done")
    assert store.list_items("open") == []
    assert [r["content"] for r in store.list_items("done")] == ["buy milk"]


def test_store_thread_safe_ingest(tmp_path):
    # V17: store written from another thread (whatsapp worker) must not error
    import threading

    s = Store(str(tmp_path / "t.db"))
    errors = []

    def writer(n):
        try:
            for i in range(20):
                s.add_message("whatsapp", "fam", f"{n}-{i}", ts=float(i), body="x")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert s.new_messages("whatsapp", "fam")  # readable cross-thread
    s.close()
