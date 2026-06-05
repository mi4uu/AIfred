"""Eval grid runner — model × sampling × cases, report hallucination + accuracy.

    uv run python -m aifred.eval.run                     # default models+samplings
    uv run python -m aifred.eval.run qwen3.6:27b gpt-oss:latest
    uv run python -m aifred.eval.run --models qwen3.6:27b --samplings greedy,qwen_rec

Prints a table per (model, sampling): accuracy, hallucination rate (overall and
on the context-grown grounding subset, which is what degrades with big context).
"""

from __future__ import annotations

import sys
import time

import httpx

from aifred.eval.cases import all_cases
from aifred.eval.harness import SAMPLINGS, call
from aifred.eval.score import score

DEFAULT_MODELS = ["qwen3.6:27b", "gpt-oss:latest"]


def run_combo(model: str, sampling, cases: list[dict], client: httpx.Client) -> dict:
    n = correct = halluc = 0
    grow_n = grow_halluc = 0
    tier_acc: dict[str, list[int]] = {}  # grounding accuracy by context tier
    # warm the model first so timings exclude the cold load-into-memory cost
    try:
        call(model, "ok", "ok", sampling, client=client)
    except httpx.HTTPError:
        pass
    lat: list[float] = []  # per-case warm latency (seconds)
    t0 = time.monotonic()
    detail = []
    for c in cases:
        c0 = time.monotonic()
        try:
            res = call(model, c["system"], c["user"], sampling, client=client)
            sc = score(c, res["text"])
        except (httpx.HTTPError, ValueError) as e:
            sc = {"correct": False, "hallucinated": False, "error": str(e)[:60]}
            res = {"text": ""}
        lat.append(time.monotonic() - c0)
        n += 1
        correct += int(sc.get("correct", False))
        halluc += int(sc.get("hallucinated", False))
        if c.get("grow"):
            grow_n += 1
            grow_halluc += int(sc.get("hallucinated", False))
        if c["task"] == "grounding":
            tier_acc.setdefault(c["tier"], []).append(int(sc.get("correct", False)))
        detail.append((c, sc, res.get("text", "")))
    tiers = {t: round(sum(v) / len(v), 2) for t, v in tier_acc.items()}
    lat_sorted = sorted(lat)
    median = lat_sorted[len(lat_sorted) // 2] if lat_sorted else 0.0
    return {
        "model": model, "sampling": sampling.name, "n": n,
        "accuracy": correct / n if n else 0,
        "halluc_rate": halluc / n if n else 0,
        "grow_halluc_rate": grow_halluc / grow_n if grow_n else 0,
        "tiers": tiers,
        "avg_warm_s": round(sum(lat) / len(lat), 1) if lat else 0,  # per-call, model already resident
        "median_warm_s": round(median, 1),
        "secs": round(time.monotonic() - t0, 1), "detail": detail,
    }


def main(argv: list[str]) -> None:
    models = [a for a in argv if not a.startswith("-")] or DEFAULT_MODELS
    sampling_filter = None
    for a in argv:
        if a.startswith("--samplings"):
            sampling_filter = set(a.split("=", 1)[1].split(",")) if "=" in a else None
    samplings = [s for s in SAMPLINGS if not sampling_filter or s.name in sampling_filter]
    cases = all_cases()
    verbose = "-v" in argv

    print(f"cases={len(cases)}  models={models}  samplings={[s.name for s in samplings]}\n")
    rows = []
    with httpx.Client(timeout=240.0) as client:
        for model in models:
            for s in samplings:
                r = run_combo(model, s, cases, client)
                rows.append(r)
                print(f"{model:<22} {s.name:<14} acc={r['accuracy']:.2f}  "
                      f"halluc={r['halluc_rate']:.2f}  grow_halluc={r['grow_halluc_rate']:.2f}  "
                      f"warm={r['avg_warm_s']}s/call (med {r['median_warm_s']}s)  ctx={r['tiers']}")
                if verbose:
                    for c, sc, txt in r["detail"]:
                        if not sc.get("correct") or sc.get("hallucinated"):
                            flag = "HALLUC" if sc.get("hallucinated") else "miss"
                            print(f"    [{flag}] {c['task']}/{c.get('grow','')} :: {txt[:80]!r}")
            print()

    print("=== ranking (low halluc, then high acc) ===")
    for r in sorted(rows, key=lambda x: (x["halluc_rate"], -x["accuracy"])):
        print(f"  {r['model']:<22} {r['sampling']:<14} "
              f"halluc={r['halluc_rate']:.2f} acc={r['accuracy']:.2f} "
              f"warm={r['avg_warm_s']}s/call")


if __name__ == "__main__":
    main(sys.argv[1:])
