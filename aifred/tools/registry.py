"""Tool registry. Holds tools; emits schemas; dispatches calls.

subset() lets the intent router scope per-turn tools (V14) so the model never
sees all tools at once — keeps schema payload small (C7).
"""

from __future__ import annotations

from typing import Any, Iterable

from aifred.tools.base import Tool, ToolError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ToolError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"unknown tool {name!r}")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self, only: Iterable[str] | None = None) -> list[dict[str, Any]]:
        names = list(only) if only is not None else self.names()
        return [self._tools[n].schema() for n in names if n in self._tools]

    def dispatch(self, name: str, args: dict[str, Any] | None) -> Any:
        return self.get(name).run(args)
