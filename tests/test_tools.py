"""T3 tool framework tests (V10 compact schema, V15 code-driven, V9 fail loud)."""

import pytest
from pydantic import BaseModel

from aifred.tools.base import DESC_MAX, Tool, ToolError, tool_from_model
from aifred.tools.registry import ToolRegistry


class AddArgs(BaseModel):
    a: int
    b: int


def _add(a: int, b: int) -> int:
    return a + b


def test_tool_from_model_compact_schema():
    t = tool_from_model("add", "add two ints", AddArgs, _add)
    sch = t.schema()
    assert sch["function"]["name"] == "add"
    assert "title" not in sch["function"]["parameters"]  # compact (C7)
    assert set(sch["function"]["parameters"]["properties"]) == {"a", "b"}


def test_description_length_capped():
    with pytest.raises(ToolError):
        Tool(name="x", description="z" * (DESC_MAX + 1), parameters={}, handler=lambda: None)


def test_run_validates_and_executes():
    t = tool_from_model("add", "add", AddArgs, _add)
    assert t.run({"a": 2, "b": 3}) == 5


def test_bad_args_fail_loud():
    # V9: bad model output raises, no silent no-op
    t = tool_from_model("add", "add", AddArgs, _add)
    with pytest.raises(ToolError):
        t.run({"a": "notint", "b": 1})


def test_registry_subset_for_router():
    r = ToolRegistry()
    r.register(tool_from_model("add", "add", AddArgs, _add))
    r.register(tool_from_model("add2", "add", AddArgs, _add))
    # V14: router scopes tool set
    assert len(r.schemas(only=["add"])) == 1
    assert r.dispatch("add", {"a": 1, "b": 1}) == 2


def test_duplicate_register_rejected():
    r = ToolRegistry()
    r.register(tool_from_model("add", "add", AddArgs, _add))
    with pytest.raises(ToolError):
        r.register(tool_from_model("add", "add", AddArgs, _add))
