"""Action-confirm layer (V7). Gate side-effecting tools.

Modes:
  deny       — block all side effects (safe default for unattended runs)
  ask        — register a pending confirmation, deny for now; an interface
               (telegram/web) surfaces it and approves/rejects out of band
  allow_all  — explicit pre-authorization for a whole session (use sparingly)

Per-tool pre-authorization lets the user say "always allow calendar_create"
without opening everything. The AgentLoop confirm hook = ConfirmManager.hook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["deny", "ask", "allow_all"]


@dataclass
class Pending:
    token: str
    tool: str
    args: dict[str, Any]


@dataclass
class ConfirmManager:
    mode: Mode = "ask"
    preauth: set[str] = field(default_factory=set)
    pending: dict[str, Pending] = field(default_factory=dict)
    _seq: int = 0

    def pre_authorize(self, tool: str) -> None:
        self.preauth.add(tool)

    def revoke(self, tool: str) -> None:
        self.preauth.discard(tool)

    def hook(self, tool: str, args: dict[str, Any]) -> bool:
        """Return True only if the action may proceed now (V7)."""
        if tool in self.preauth:
            return True
        if self.mode == "allow_all":
            return True
        if self.mode == "deny":
            return False
        # ask: stage a pending confirmation, deny for this turn
        self._seq += 1
        token = f"cf{self._seq}"
        self.pending[token] = Pending(token=token, tool=tool, args=dict(args))
        return False

    def list_pending(self) -> list[Pending]:
        return list(self.pending.values())

    def approve(self, token: str) -> Pending:
        if token not in self.pending:
            raise KeyError(f"no pending confirmation {token!r}")
        return self.pending.pop(token)

    def reject(self, token: str) -> Pending:
        return self.approve(token)  # same removal; caller treats as rejected
