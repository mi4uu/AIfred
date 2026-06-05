"""Agentic tool-use eval (V30 lesson) — the dimension the single-shot eval missed.

qwen3-vl:8b passed extract/triage but, asked to "zanotuj X", called journal_add
with the wrong arg and then FABRICATED success. This harness tests exactly that:
given the real tool schemas + a Polish instruction, does the model emit the RIGHT
tool call with the RIGHT required args? No fabrication credit — narrating instead
of calling = fail.

    uv run python -m aifred.eval.agentic qwen3.6:27b gemma4:latest qwen3-vl:8b
"""

from __future__ import annotations

import sys
import time

import httpx

OLLAMA = "http://localhost:11434"

# the real tools the agent must drive (compact JSON schema, like the registry emits)
TOOLS = [
    {"type": "function", "function": {
        "name": "journal_add", "description": "dopisz wpis do dziennika w brain.md",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "co zapisać"}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "calendar_create", "description": "utwórz wydarzenie w kalendarzu",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
            "source": {"type": "string"}}, "required": ["summary", "start", "source"]}}},
    {"type": "function", "function": {
        "name": "attention_list", "description": "co wymaga uwagi (triaged mail+whatsapp)",
        "parameters": {"type": "object", "properties": {
            "importance": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "task_add", "description": "dodaj zadanie do zrobienia",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}}, "required": ["title"]}}},
]

# (instruction, expected_tool, required_arg_that_must_be_nonempty)
CASES = [
    ("Zanotuj w dzienniku: kupiłem mleko i chleb.", "journal_add", "text"),
    ("Zapisz proszę, że dzwoniłem do banku.", "journal_add", "text"),
    ("Co mam dziś ważnego do ogarnięcia?", "attention_list", None),
    ("Dodaj zadanie: oddać książki do biblioteki.", "task_add", "title"),
    ("Co tam na dziś / co wymaga uwagi?", "attention_list", None),
    ("Zanotuj że Zosia ma dentystę.", "journal_add", "text"),
]


def _call(client, model, instr):
    r = client.post(f"{OLLAMA}/api/chat", json={
        "model": model, "stream": False, "think": False, "keep_alive": "30m",
        "messages": [
            {"role": "system", "content": "Jesteś asystentem z narzędziami. Gdy użytkownik prosi o "
             "zapisanie/dodanie/sprawdzenie — WYWOŁAJ właściwe narzędzie z poprawnymi argumentami. "
             "Nie udawaj że coś zrobiłeś bez wywołania narzędzia."},
            {"role": "user", "content": instr}],
        "tools": TOOLS, "options": {"temperature": 0, "num_ctx": 8192, "seed": 42}})
    r.raise_for_status()
    return (r.json().get("message", {}) or {})


def _score(msg, expected, req):
    tcs = msg.get("tool_calls") or []
    if not tcs:
        return False, "no tool_call (narrated?)"
    fn = tcs[0].get("function", {})
    if fn.get("name") != expected:
        return False, f"wrong tool: {fn.get('name')}"
    if req:
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            import json
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not str(args.get(req, "")).strip():
            return False, f"missing arg '{req}': {args}"
    return True, "ok"


def run_model(model, client):
    ok = 0
    fails = []
    t0 = time.monotonic()
    for instr, exp, req in CASES:
        try:
            msg = _call(client, model, instr)
            good, why = _score(msg, exp, req)
        except (httpx.HTTPError, ValueError) as e:
            good, why = False, f"err {str(e)[:40]}"
        ok += int(good)
        if not good:
            fails.append(f"  [{exp}] {instr[:34]!r} -> {why}")
    return {"model": model, "score": ok / len(CASES), "ok": ok, "n": len(CASES),
            "secs": round(time.monotonic() - t0, 1), "fails": fails}


def main(argv):
    models = argv or ["qwen3.6:27b"]
    with httpx.Client(timeout=240.0) as client:
        rows = []
        for m in models:
            r = run_model(m, client)
            rows.append(r)
            print(f"{m:<26} agentic_tool_use={r['score']:.2f} ({r['ok']}/{r['n']})  {r['secs']}s")
            for f in r["fails"]:
                print(f)
            print()
        print("=== ranking (agentic) ===")
        for r in sorted(rows, key=lambda x: -x["score"]):
            print(f"  {r['model']:<26} {r['score']:.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
