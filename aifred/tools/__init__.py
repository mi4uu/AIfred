"""Tool framework — typed tools, compact schemas, no prose injection (V10)."""

from aifred.tools.base import Tool, ToolError, tool_from_model
from aifred.tools.registry import ToolRegistry

__all__ = ["Tool", "ToolError", "tool_from_model", "ToolRegistry"]
