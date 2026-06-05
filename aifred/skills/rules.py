"""Triage rule tools — how the agent LEARNS importance preferences from you.

"AIfred, ignoruj grupę STO" / "wiadomości od Anny nieważne" / "Kasia zawsze
ważna" -> a deterministic rule the triage engine applies before/over the LLM.
Same rules are editable in the UI. This is the teaching/correction mechanism.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

from aifred.store.db import Store
from aifred.tools.base import Tool, ToolError, tool_from_model

SCOPES = {"sender", "group", "domain", "category"}
ACTIONS = {"mute", "vip", "high", "medium", "low"}


def triage_rule(store: Store, action: str, scope: str, pattern: str) -> dict:
    action, scope = action.lower().strip(), scope.lower().strip()
    if scope not in SCOPES:
        raise ToolError(f"scope must be one of {sorted(SCOPES)}")
    if action not in ACTIONS:
        raise ToolError(f"action must be one of {sorted(ACTIONS)}")
    if not pattern.strip():
        raise ToolError("pattern required (e.g. a name, group, domain or keyword)")
    rid = store.add_rule(scope, pattern, action, time.time())
    return {"ok": True, "id": rid, "rule": f"{action} {scope}={pattern}"}


def triage_rules_list(store: Store) -> dict:
    return {"rules": store.list_rules()}


class RuleArgs(BaseModel):
    action: str = Field(description="mute (ignore) | vip (always important) | high | medium | low")
    scope: str = Field(description="sender | group | domain | category")
    pattern: str = Field(description="who/what to match: a person name, group, email domain, or keyword")


class EmptyArgs(BaseModel):
    pass


def build_rule_tools(store: Store) -> list[Tool]:
    return [
        tool_from_model(
            "triage_rule",
            "learn an importance preference (mute/vip/level) for a sender/group/domain/keyword",
            RuleArgs, lambda action, scope, pattern: triage_rule(store, action, scope, pattern),
            tags=("core",),
        ),
        tool_from_model(
            "triage_rules_list", "list current triage rules", EmptyArgs,
            lambda: triage_rules_list(store), tags=("core",),
        ),
    ]
