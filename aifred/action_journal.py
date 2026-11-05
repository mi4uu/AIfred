"""Durable action journal for crash-safe side-effecting tool calls.

The confirm layer (confirm.py) decides WHETHER an action may run. This module
makes running it safe against a crash in between: AIfred approves a calendar
write, the process executes it, then dies (power loss, OOM kill, deploy)
before it can tell anyone the write happened. Two bad outcomes we must avoid
on restart:
  - repeating the action (the owner now has two calendar events), or
  - losing it silently (the owner approved something that never happened and
    nothing says so).

Every side-effecting call gets a fresh idempotency key and a journal row
*before* it runs ("intent"), and the row is updated to "done"/"failed" only
after it returns ("completion"). The row lives in the same sqlite `Store`
AIfred already uses, so it survives the crash. On startup, `reconcile()`
walks whatever is still "started" (the process died somewhere between intent
and completion, for either row, we can't tell which from the row alone) and
retries each one exactly once through the same tool. That retry is itself
journaled, so a second crash doesn't retry forever.

This only prevents AIfred's own bookkeeping from duplicating or dropping a
call. Whether the retry is safe against the EXTERNAL system (Gmail, Calendar)
seeing the call twice is up to that system: pass the idempotency key down
wherever the API accepts one (e.g. Calendar's `requestId`), so a repeated call
lands on the same remote object instead of creating a second one. A tool with
no such key support is still journaled correctly, it just also needs to be
safe to call twice, same as it would without this module.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aifred.store.db import Store


@dataclass
class ReconcileResult:
    key: str
    tool: str
    outcome: str  # "retried" | "retry_failed" | "unresolved" (no handler registered for this tool)
    error: str = ""


def merge_key_if_supported(parameters: dict[str, Any], args: dict[str, Any], key: str) -> dict[str, Any]:
    """Add `idempotency_key` into the call args only if this tool's own JSON
    schema declares that property (duck-typed opt-in). A tool whose handler
    talks to an external API that accepts a dedup key (Calendar's `requestId`,
    a mail `Message-Id`) gets ours passed straight through; a tool with no
    such parameter is called with its args untouched."""
    props = parameters.get("properties") or {}
    if "idempotency_key" in props:
        return {**args, "idempotency_key": key}
    return args


@dataclass
class ActionJournal:
    store: Store
    clock: Callable[[], float] = field(default=time.time)

    def new_key(self) -> str:
        return uuid.uuid4().hex

    def run(self, key: str, tool: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        """Execute fn() under `key`, recording intent first and completion after.

        If `key` already has a "done" row (fn already ran to completion under
        this exact key, e.g. this is reconcile() retrying something that in
        fact finished before the crash), returns the recorded result without
        calling fn() again. Any other state (new key, or a leftover "started"/
        "failed" from a prior crash) calls fn() once.
        """
        is_new = self.store.journal_start(key, tool, args, self.clock())
        if not is_new:
            row = self.store.journal_get(key)
            if row is not None and row["status"] == "done":
                return json.loads(row["result"]) if row["result"] else None
        try:
            result = fn()
        except Exception as e:
            self.store.journal_fail(key, str(e), self.clock())
            raise
        self.store.journal_finish(key, result, self.clock())
        return result

    def reconcile(self, tools: dict[str, Callable[[dict[str, Any], str], Any]]) -> list[ReconcileResult]:
        """Call at startup, before anything else touches these tools. For every
        journal row still "started" (left over from a crash between intent and
        completion), retries it once via `tools[row.tool](args, key)`, journaled
        under the SAME key so this is itself crash-safe. A tool with no handler
        in `tools` is reported "unresolved" rather than skipped silently."""
        out: list[ReconcileResult] = []
        for row in self.store.journal_unfinished():
            key, tool = row["idempotency_key"], row["tool"]
            handler = tools.get(tool)
            if handler is None:
                out.append(ReconcileResult(key=key, tool=tool, outcome="unresolved"))
                continue
            args = json.loads(row["args"])

            def retry(a: dict[str, Any] = args, h: Callable[[dict[str, Any], str], Any] = handler,
                      k: str = key) -> Any:
                return h(a, k)

            try:
                self.run(key, tool, args, retry)
                out.append(ReconcileResult(key=key, tool=tool, outcome="retried"))
            except Exception as e:
                out.append(ReconcileResult(key=key, tool=tool, outcome="retry_failed", error=str(e)[:200]))
        return out
