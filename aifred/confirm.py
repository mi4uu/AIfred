"""Action-confirm layer (V7). Gate side-effecting tools.

Modes:
  deny      , block all side effects (safe default for unattended runs)
  ask       , register a pending confirmation, deny for now; an interface
               (telegram/web) surfaces it and approves/rejects out of band
  allow_all , explicit pre-authorization for a whole session (use sparingly)

Per-tool pre-authorization lets the user say "always allow calendar_create"
without opening everything. The AgentLoop confirm hook = ConfirmManager.hook.

Pending confirmations expire (ttl_seconds, default 15 min): an "ask" prompt
the owner never answers must not stay approvable forever, especially once a
tool call has been echoed to Telegram/web with a tappable token. `clock` is
injectable so tests can move time without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["deny", "ask", "allow_all"]

DEFAULT_TTL_SECONDS = 15 * 60  # sensible default: long enough to notice a Telegram ping, short enough to matter


class ConfirmExpiredError(Exception):
    """Raised by approve() for a token whose ttl has passed. The action never ran."""

    def __init__(self, token: str, tool: str):
        self.token = token
        self.tool = tool
        super().__init__(f"confirmation {token!r} for tool {tool!r} expired, ask again")


@dataclass
class Pending:
    token: str
    tool: str
    args: dict[str, Any]
    created_at: float = 0.0
    expires_at: float = 0.0


@dataclass
class ConfirmManager:
    mode: Mode = "ask"
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = field(default=time.time)
    preauth: set[str] = field(default_factory=set)
    pending: dict[str, Pending] = field(default_factory=dict)
    _seq: int = 0

    def pre_authorize(self, tool: str) -> None:
        self.preauth.add(tool)

    def revoke(self, tool: str) -> None:
        self.preauth.discard(tool)

    def hook(self, tool: str, args: dict[str, Any]) -> bool:
        """Return True only if the action may proceed now (V7)."""
        self.cleanup_expired()
        if tool in self.preauth:
            return True
        if self.mode == "allow_all":
            return True
        if self.mode == "deny":
            return False
        # ask: stage a pending confirmation, deny for this turn
        self._seq += 1
        token = f"cf{self._seq}"
        now = self.clock()
        self.pending[token] = Pending(
            token=token, tool=tool, args=dict(args), created_at=now, expires_at=now + self.ttl_seconds
        )
        return False

    def list_pending(self) -> list[Pending]:
        self.cleanup_expired()
        return list(self.pending.values())

    def cleanup_expired(self) -> list[Pending]:
        """Drop pending confirmations past their ttl. Returns what was dropped
        so a caller (webapi) can tell the owner those prompts are gone."""
        now = self.clock()
        expired = [p for p in self.pending.values() if now >= p.expires_at]
        for p in expired:
            del self.pending[p.token]
        return expired

    def approve(self, token: str) -> Pending:
        p = self.pending.get(token)
        if p is None:
            raise KeyError(f"no pending confirmation {token!r}")
        if self.clock() >= p.expires_at:
            del self.pending[token]
            raise ConfirmExpiredError(token, p.tool)
        del self.pending[token]
        return p

    def reject(self, token: str) -> Pending:
        # rejection never runs the action either way, so an expired token is
        # still a valid thing to reject (just clears it, no error needed)
        if token not in self.pending:
            raise KeyError(f"no pending confirmation {token!r}")
        return self.pending.pop(token)
