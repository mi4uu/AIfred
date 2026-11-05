"""Agent loop. Router scopes tools (V14) → LLM → dispatch tool calls → repeat.

Budget guard sits in LLMClient (C7). Tool results appended as role=tool msgs.
Stops on no tool_calls or max_iters. Side-effecting tools gated by a confirm
hook (V7) injected by the caller (T15).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from aifred.action_journal import ActionJournal, merge_key_if_supported
from aifred.agent.router import IntentRouter
from aifred.llm.client import LLMClient
from aifred.tools.base import ToolError
from aifred.tools.registry import ToolRegistry


class AgentLike(Protocol):
    """What callers outside the agent package need: run a turn, get a reply."""

    def run(self, user_message: str, history: list[dict] | None = None, on_step: Any = None) -> Any: ...

DEFAULT_SYSTEM = (
    "You are AIfred, a personal assistant. Use tools to act. "
    "Be terse. Never invent data; if a tool can fetch it, call the tool."
)

# confirm hook: (tool_name, args) -> bool. Default deny side effects unless overridden.
ConfirmHook = Callable[[str, dict], bool]


def _deny(_name: str, _args: dict) -> bool:
    return False


@dataclass
class AgentLoop:
    llm: LLMClient
    registry: ToolRegistry
    router: IntentRouter
    system_prompt: str = DEFAULT_SYSTEM
    max_iters: int = 6
    confirm: ConfirmHook = field(default=_deny)
    journal: ActionJournal | None = None  # crash-safe side-effecting calls (None = old direct-call behavior)

    def run(self, user_message: str, history: list[dict] | None = None, on_step=None) -> dict:
        """on_step(event, detail) optional, emits 'thinking'/'tool'/'done' for SSE."""
        def step(event: str, detail: str = "") -> None:
            if on_step:
                on_step(event, detail)

        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        messages.append({"role": "system", "content": _now_hint()})  # V35: ground the date so models don't guess the year
        messages += history or []
        messages.append({"role": "user", "content": user_message})

        tool_names = self.router.select(user_message)  # V14
        schemas = self.registry.schemas(only=tool_names)

        for _ in range(self.max_iters):
            step("thinking")
            res = self.llm.chat(messages, tools=schemas or None)
            if not res.tool_calls:
                messages.append({"role": "assistant", "content": res.content})
                step("done", res.content)
                return {"reply": res.content, "messages": messages}

            messages.append(
                {"role": "assistant", "content": res.content or "", "tool_calls": res.tool_calls}
            )
            for call in res.tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                step("tool", name)
                args = _parse_args(fn.get("arguments"))
                out = self._invoke(name, args) if args is not None else "ERROR bad tool arguments"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", name),
                        "name": name,
                        "content": _stringify(out),
                    }
                )
        step("done", "(max tool iterations reached)")
        return {"reply": "(max tool iterations reached)", "messages": messages}

    def _invoke(self, name: str, args: dict):
        try:
            tool = self.registry.get(name)
        except ToolError as e:
            return f"ERROR {e}"  # V9
        if tool.side_effecting and not self.confirm(name, args):  # V7
            return "DENIED: side-effecting action not confirmed"
        try:
            if tool.side_effecting and self.journal is not None:
                key = self.journal.new_key()
                call_args = merge_key_if_supported(tool.parameters, args, key)
                return self.journal.run(key, name, args, lambda: tool.run(call_args))
            return tool.run(args)
        except ToolError as e:
            return f"ERROR {e}"  # V9 fail loud, loop continues
        except Exception as e:
            return f"ERROR {type(e).__name__}: {str(e)[:200]}"


_WEEKDAYS = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def _now_hint(tz_name: str | None = None) -> str:
    """Current date/time in the owner's configured timezone (AIFRED_TIMEZONE,
    default Europe/Warsaw) so the model builds correct calendar dates instead
    of guessing the year (V35).

    Deliberately does NOT tell the model "today's" UTC offset for use on
    calendar events: an event on the other side of a DST change from today
    would get the wrong one. Instead the model gives a naive local date+time
    with no offset, and calendar_create/calendar_update attach the correct
    offset for THAT event's own date in code (aifred.tz, per-date zoneinfo).
    """
    from aifred.tz import now_in_tz

    now = now_in_tz(tz_name)
    zone = tz_name or getattr(now.tzinfo, "key", str(now.tzinfo))
    return (f"Aktualna data: {now.strftime('%Y-%m-%d')} ({_WEEKDAYS[now.weekday()]}), "
            f"godzina {now.strftime('%H:%M')}, strefa {zone}. Dla wydarzeń w kalendarzu podawaj "
            f"datę i czas lokalny BEZ offsetu (np. 2026-06-09T16:40), system sam dołączy "
            f"poprawny offset dla tej konkretnej daty. ZAWSZE bieżący rok ({now.year}) o ile nie podano innego.")


def _parse_args(raw):
    """Tool args: native ollama gives a dict, OpenAI-compat gives a JSON string."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None  # signals bad args (V9)


def _stringify(out) -> str:
    if isinstance(out, str):
        return out
    try:
        return json.dumps(out, default=str)
    except (TypeError, ValueError):
        return str(out)
