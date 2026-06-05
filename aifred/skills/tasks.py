"""Task tracker skill (V2, V12).

brain.md MCP is append-only, so mutable todo/done STATE lives in the store
(items table) while brain.md gets an append log line on add/complete — brain
stays the canonical human-readable record (V2), store is the queryable state.
Recall of related notes uses retrieval (V12).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from aifred.store.db import Store
from aifred.tools.base import Tool, ToolError, tool_from_model


def task_add(store: Store, content: str, brain=None, now_ts: float = 0.0) -> dict:
    tid = store.add_item("tasks", "todo", content, now_ts)
    if brain is not None:
        brain.append(f"TODO #{tid}: {content}")  # V2 log
    return {"id": tid, "content": content, "status": "open"}


def task_list(store: Store, status: str = "open") -> list[dict]:
    return [{"id": r["id"], "content": r["content"], "status": r["status"]} for r in store.list_items(status)]


def task_done(store: Store, task_id: int, brain=None) -> dict:
    rows = {r["id"]: r for r in store.list_items("open")}
    if task_id not in rows:
        raise ToolError(f"no open task #{task_id}")  # fail loud (V9)
    store.set_item_status(task_id, "done")
    if brain is not None:
        brain.append(f"DONE #{task_id}: {rows[task_id]['content']}")  # V2 log
    return {"id": task_id, "status": "done"}


class TaskAddArgs(BaseModel):
    content: str = Field(description="task description")


class TaskListArgs(BaseModel):
    status: str = Field(default="open", description="open or done")


class TaskDoneArgs(BaseModel):
    task_id: int


def build_task_tools(store: Store, brain=None) -> list[Tool]:
    return [
        tool_from_model(
            "task_add", "add a todo (logged to brain.md)", TaskAddArgs,
            lambda content: task_add(store, content, brain), tags=("tasks",),
        ),
        tool_from_model(
            "task_list", "list open or done tasks", TaskListArgs,
            lambda status: task_list(store, status), tags=("tasks",),
        ),
        tool_from_model(
            "task_done", "mark a task done", TaskDoneArgs,
            lambda task_id: task_done(store, task_id, brain), tags=("tasks",),
        ),
    ]
