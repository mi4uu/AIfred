"""Crash-safety tests for the durable action journal (action_journal.py).

Each crash scenario is simulated the same way: do part of the journal.run()
sequence by hand (what would have happened before the process died), close
the store, reopen a fresh Store over the same file (the "restart"), and build
a new ActionJournal over it before asserting what reconcile() does.
"""

from __future__ import annotations

import pytest

from aifred.action_journal import ActionJournal, merge_key_if_supported
from aifred.store.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


class FakeExternalSystem:
    """Stands in for Gmail/Calendar/etc. Dedupes by idempotency key the same
    way a real API with a `requestId`/dedup key would, so a retried call that
    already landed does not create a second side effect."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []  # every call that actually ran
        self._results: dict[str, dict] = {}

    def create(self, args: dict, key: str) -> dict:
        if key in self._results:
            return self._results[key]  # already happened under this key, no-op
        self.calls.append((key, dict(args)))
        result = {"id": f"ext-{len(self.calls)}", "echo": args}
        self._results[key] = result
        return result


# --- basic run() correctness (no crash) ---


def test_run_executes_once_and_journals_done(store):
    ext = FakeExternalSystem()
    journal = ActionJournal(store)
    key = journal.new_key()
    result = journal.run(key, "calendar_create", {"summary": "x"}, lambda: ext.create({"summary": "x"}, key))
    assert result["echo"] == {"summary": "x"}
    assert len(ext.calls) == 1
    row = store.journal_get(key)
    assert row["status"] == "done"


def test_run_with_same_key_twice_returns_cached_result_without_rerunning_fn(store):
    """Calling run() again under a key already marked done must not call fn
    again: fn here would append to `calls`, so a second append means a bug."""
    ext = FakeExternalSystem()
    journal = ActionJournal(store)
    key = journal.new_key()
    journal.run(key, "calendar_create", {"summary": "x"}, lambda: ext.create({"summary": "x"}, key))
    second = journal.run(key, "calendar_create", {"summary": "x"}, lambda: ext.create({"summary": "x"}, key))
    assert len(ext.calls) == 1  # fn did not run again
    assert second["echo"] == {"summary": "x"}


def test_run_records_failure_and_reraises(store):
    journal = ActionJournal(store)
    key = journal.new_key()

    def boom():
        raise RuntimeError("downstream exploded")

    with pytest.raises(RuntimeError):
        journal.run(key, "send_mail", {"to": "x"}, boom)
    row = store.journal_get(key)
    assert row["status"] == "failed"
    assert "downstream exploded" in row["result"]


# --- crash scenarios, store survives on disk across a simulated restart ---


def test_crash_after_side_effect_before_marked_done_does_not_duplicate_on_restart(tmp_path):
    """The real failure this guards against: journal_start() committed, the
    external call happened, then the process died before journal_finish().
    On restart, reconcile() retries the row; the external system's own key
    dedup must mean the retried call does not create a second real event."""
    db_path = tmp_path / "aifred.db"
    ext = FakeExternalSystem()

    store1 = Store(str(db_path))
    journal1 = ActionJournal(store1)
    key = journal1.new_key()
    args = {"summary": "dentist"}
    store1.journal_start(key, "calendar_create", args, ts=1.0)
    ext.create(args, key)  # the side effect actually happened...
    # ...but the process dies right here, before journal_finish() ever runs
    store1.close()

    # restart: fresh Store + ActionJournal over the same file
    store2 = Store(str(db_path))
    journal2 = ActionJournal(store2)
    assert len(store2.journal_unfinished()) == 1

    report = journal2.reconcile(tools={"calendar_create": lambda a, k: ext.create(a, k)})

    assert [r.outcome for r in report] == ["retried"]
    assert len(ext.calls) == 1  # NOT duplicated: the external system recognised the same key
    row = store2.journal_get(key)
    assert row["status"] == "done"
    assert store2.journal_unfinished() == []  # nothing left dangling
    store2.close()


def test_crash_before_execution_is_retried_not_lost_on_restart(tmp_path):
    """journal_start() committed (intent recorded) but the process died before
    fn() ever ran, so the external system has no record of it at all. Restart
    must still perform the action exactly once, not drop it."""
    db_path = tmp_path / "aifred.db"
    ext = FakeExternalSystem()

    store1 = Store(str(db_path))
    key = ActionJournal(store1).new_key()
    args = {"summary": "pay invoice"}
    store1.journal_start(key, "calendar_create", args, ts=1.0)
    # crash here: fn() never ran, ext.calls is still empty
    store1.close()
    assert ext.calls == []

    store2 = Store(str(db_path))
    journal2 = ActionJournal(store2)
    report = journal2.reconcile(tools={"calendar_create": lambda a, k: ext.create(a, k)})

    assert [r.outcome for r in report] == ["retried"]
    assert len(ext.calls) == 1  # performed exactly once, not silently dropped
    assert store2.journal_get(key)["status"] == "done"
    store2.close()


def test_reconcile_surfaces_unresolved_entries_instead_of_dropping_them(tmp_path):
    """A leftover row whose tool has no handler in this run (e.g. that tool
    was removed, or Google creds aren't loaded this time) must be reported,
    never silently ignored."""
    db_path = tmp_path / "aifred.db"
    store1 = Store(str(db_path))
    key = ActionJournal(store1).new_key()
    store1.journal_start(key, "gone_tool", {"x": 1}, ts=1.0)
    store1.close()

    store2 = Store(str(db_path))
    journal2 = ActionJournal(store2)
    report = journal2.reconcile(tools={})  # no handler registered for "gone_tool"

    assert [(r.tool, r.outcome) for r in report] == [("gone_tool", "unresolved")]
    assert store2.journal_unfinished() != []  # still there, waiting, not discarded
    store2.close()


def test_reconcile_marks_retry_failed_when_the_retry_itself_errors(tmp_path):
    db_path = tmp_path / "aifred.db"
    store1 = Store(str(db_path))
    key = ActionJournal(store1).new_key()
    store1.journal_start(key, "flaky", {"x": 1}, ts=1.0)
    store1.close()

    store2 = Store(str(db_path))
    journal2 = ActionJournal(store2)

    def always_fails(_args, _key):
        raise RuntimeError("still down")

    report = journal2.reconcile(tools={"flaky": always_fails})
    assert report[0].outcome == "retry_failed"
    assert "still down" in report[0].error
    assert store2.journal_get(key)["status"] == "failed"  # surfaced, not left ambiguous
    store2.close()


def test_reconcile_on_a_clean_store_with_no_leftovers_does_nothing(store):
    journal = ActionJournal(store)
    assert journal.reconcile(tools={}) == []


# --- idempotency key passed through to tools that declare it (duck-typed opt-in) ---


def test_merge_key_if_supported_adds_key_only_when_schema_declares_it():
    with_key_schema = {"properties": {"idempotency_key": {"type": "string"}, "summary": {"type": "string"}}}
    without_key_schema = {"properties": {"summary": {"type": "string"}}}
    args = {"summary": "x"}
    key = "abc123"

    assert merge_key_if_supported(with_key_schema, args, key) == {"summary": "x", "idempotency_key": key}
    assert merge_key_if_supported(without_key_schema, args, key) == {"summary": "x"}
    # original dict is never mutated in place
    assert args == {"summary": "x"}
