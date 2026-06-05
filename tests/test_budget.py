"""T2 budget guard tests (C7, V11)."""

import pytest

from aifred.llm.budget import BudgetExceeded, BudgetGuard, count_messages


def msg(role, n):
    return {"role": role, "content": "x" * n}


def test_fits_under_limit():
    g = BudgetGuard(limit=100)
    assert g.fits([msg("user", 40)])  # ~14 tokens


def test_trim_drops_oldest_middle():
    g = BudgetGuard(limit=30)  # ~120 chars budget
    messages = [
        msg("system", 20),
        msg("user", 200),  # old, big — should drop
        msg("assistant", 20),
        msg("user", 20),  # last — kept
    ]
    out = g.trim(messages)
    assert out[0]["role"] == "system"  # system kept
    assert out[-1] == messages[-1]  # last kept
    assert count_messages(out) <= 30
    assert messages[1] not in out  # oldest big dropped


def test_raises_when_minimal_overflows():
    g = BudgetGuard(limit=5)
    with pytest.raises(BudgetExceeded):
        g.trim([msg("system", 4000), msg("user", 4000)])
