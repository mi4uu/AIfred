"""T12 task tracker tests (V2 brain log, V9 fail-loud)."""

import pytest

from aifred.skills.tasks import build_task_tools, task_add, task_done, task_list
from aifred.store.db import Store
from aifred.tools.base import ToolError


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


class FakeBrain:
    def __init__(self):
        self.appended = []

    def append(self, content):
        self.appended.append(content)


def test_add_list_done_cycle(store):
    b = FakeBrain()
    t = task_add(store, "buy milk", b)
    assert task_list(store, "open")[0]["content"] == "buy milk"
    assert any("TODO" in a for a in b.appended)  # V2 log
    task_done(store, t["id"], b)
    assert task_list(store, "open") == []
    assert task_list(store, "done")[0]["content"] == "buy milk"
    assert any("DONE" in a for a in b.appended)  # V2 log


def test_done_unknown_fails_loud(store):
    with pytest.raises(ToolError):  # V9
        task_done(store, 999)


def test_tools_tagged(store):
    tools = {t.name: t for t in build_task_tools(store)}
    assert set(tools) == {"task_add", "task_list", "task_done"}
    assert all(t.tags == ("tasks",) for t in tools.values())
