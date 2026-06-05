"""Tool = typed callable exposed to LLM as compact JSON schema (V10).

Skill logic lives in the handler (code), NOT in prompt prose (V15). The model
sees only {name, short description, params schema} — never instructions.

DESC_MAX caps description length so schemas stay compact (C7). Args validated
by a pydantic model before the handler runs — bad model output fails loud (V9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

DESC_MAX = 200  # keep tool descriptions terse — context budget (C7)


class ToolError(Exception):
    """Tool failure surfaced to caller/LLM, never silent (V9)."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema (object)
    handler: Callable[..., Any]
    args_model: type[BaseModel] | None = None
    side_effecting: bool = False  # gated by confirm layer (V7, T15)
    tags: tuple[str, ...] = ()  # router scoping (V14); "core" = always loaded

    def __post_init__(self) -> None:
        if len(self.description) > DESC_MAX:
            raise ToolError(f"tool {self.name!r} description {len(self.description)} > {DESC_MAX} (C7)")

    def schema(self) -> dict[str, Any]:
        """OpenAI tool-calling format. Compact — no prose body."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, args: dict[str, Any] | None) -> Any:
        args = args or {}
        if self.args_model is not None:
            try:
                validated = self.args_model(**args)
            except ValidationError as e:
                raise ToolError(f"{self.name}: bad args: {e}") from e
            return self.handler(**validated.model_dump())
        return self.handler(**args)


def tool_from_model(
    name: str,
    description: str,
    args_model: type[BaseModel],
    handler: Callable[..., Any],
    *,
    side_effecting: bool = False,
    tags: tuple[str, ...] = (),
) -> Tool:
    """Build a Tool, deriving the params schema from a pydantic model.

    Strips pydantic's verbose keys so the emitted schema stays compact (C7).
    """
    schema = args_model.model_json_schema()
    schema.pop("title", None)
    for prop in (schema.get("properties") or {}).values():
        prop.pop("title", None)
    return Tool(
        name=name,
        description=description,
        parameters=schema,
        handler=handler,
        args_model=args_model,
        side_effecting=side_effecting,
        tags=tags,
    )
