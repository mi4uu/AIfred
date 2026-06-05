"""Scorers — turn a model reply into correct/hallucinated booleans per task."""

from __future__ import annotations

import re

from aifred.eval.harness import parse_json


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _needle(gold: str, text: str) -> bool:
    """Tolerant match: exact substring OR same alphanumeric run (16.40 == 16:40)."""
    if _norm(gold) in _norm(text):
        return True
    g = re.sub(r"[^0-9a-ząćęłńóśźż]", "", gold.lower())
    t = re.sub(r"[^0-9a-ząćęłńóśźż]", "", text.lower())
    return bool(g) and g in t


def score_grounding(case: dict, text: str) -> dict:
    said_dont_know = "nie_wiem" in _norm(text)
    if case["gold"] is None:
        # must refuse. answering = hallucination.
        return {"correct": said_dont_know, "hallucinated": not said_dont_know}
    hit = _needle(case["gold"], text) and not said_dont_know
    # wrongly refusing an answerable Q is a miss but NOT a hallucination
    return {"correct": hit, "hallucinated": False}


def score_extract(case: dict, text: str) -> dict:
    data = parse_json(text)
    if not isinstance(data, list):
        return {"correct": not case["must"], "hallucinated": False, "parse_fail": True}
    got = {str(d.get("date")) for d in data if isinstance(d, dict) and d.get("date")}
    must = case["must"]
    missing = must - got
    invented = {g for g in got if g and g not in must}  # date not in the source = hallucination
    return {"correct": not missing and not invented,
            "hallucinated": bool(invented), "missing": sorted(missing), "invented": sorted(invented)}


def score_triage(case: dict, text: str) -> dict:
    data = parse_json(text)
    if not isinstance(data, dict):
        return {"correct": False, "hallucinated": False, "parse_fail": True}
    imp = str(data.get("importance", "")).lower()
    directed = data.get("directed_at_me")
    imp_ok = imp in case["imp"]
    dir_ok = case["directed"] is None or bool(directed) == case["directed"]
    # promoting undirected group chatter to high/medium == a false alarm (hallucinated importance)
    false_alarm = case["directed"] is False and imp in ("high", "medium")
    return {"correct": imp_ok and dir_ok, "hallucinated": false_alarm}


SCORERS = {"grounding": score_grounding, "extract": score_extract, "triage": score_triage}


def score(case: dict, text: str) -> dict:
    return SCORERS[case["task"]](case, text)
