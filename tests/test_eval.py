"""Eval harness scorers — deterministic, no ollama calls."""

from aifred.eval.harness import parse_json
from aifred.eval.score import score_extract, score_grounding, score_triage


def test_parse_json_handles_fences_and_prose():
    assert parse_json('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert parse_json('tu masz: {"x": 2} koniec') == {"x": 2}
    assert parse_json("no json here") is None


def test_grounding_refusal_is_correct_not_halluc():
    case = {"gold": None}
    assert score_grounding(case, "NIE_WIEM") == {"correct": True, "hallucinated": False}
    # inventing an answer when it must refuse = hallucination
    r = score_grounding(case, "Numer to 600 100 200")
    assert r["correct"] is False and r["hallucinated"] is True


def test_grounding_tolerant_needle():
    case = {"gold": "16.40"}
    assert score_grounding(case, "O 16:40 u dentysty")["correct"] is True  # 16.40 == 16:40
    assert score_grounding(case, "NIE_WIEM")["correct"] is False


def test_extract_invented_date_is_halluc():
    case = {"must": {"2026-06-09"}}
    ok = score_extract(case, '[{"summary":"x","date":"2026-06-09"}]')
    assert ok["correct"] and not ok["hallucinated"]
    bad = score_extract(case, '[{"summary":"x","date":"2026-06-09"},{"summary":"y","date":"2026-12-31"}]')
    assert bad["hallucinated"] and not bad["correct"]  # 12-31 not in source
    miss = score_extract(case, "[]")
    assert not miss["correct"] and not miss["hallucinated"]  # missing != invented


def test_triage_group_false_alarm_is_halluc():
    # undirected group chatter promoted to high = false alarm
    case = {"imp": {"low"}, "directed": False}
    fa = score_triage(case, '{"importance":"high","directed_at_me":false}')
    assert fa["hallucinated"] and not fa["correct"]
    ok = score_triage(case, '{"importance":"low","directed_at_me":false}')
    assert ok["correct"] and not ok["hallucinated"]
