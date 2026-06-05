"""Raw ollama caller for evaluation — full control over model + sampling.

Bypasses LLMClient so each call can set a different model and options
(temperature/top_p/top_k/repeat_penalty/num_ctx/seed), think=false, and a
fixed seed for repeatability. Returns (text, eval_count, prompt_eval_count).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

OLLAMA = "http://localhost:11434"
SEED = 42  # fixed for repeatable runs


@dataclass(frozen=True)
class Sampling:
    name: str
    options: dict = field(default_factory=dict)

    def merged(self) -> dict:
        return {"seed": SEED, **self.options}


# sweep grid — from greedy to Qwen's recommended non-thinking settings, with/without
# an explicit num_ctx (ollama's runtime default is 4096 -> truncates big prompts).
SAMPLINGS = [
    Sampling("greedy", {"temperature": 0.0}),                                   # current behaviour
    Sampling("greedy_ctx16k", {"temperature": 0.0, "num_ctx": 16384}),          # greedy + room for context
    Sampling("low_faithful", {"temperature": 0.2, "top_p": 0.9, "top_k": 20,
                              "repeat_penalty": 1.05, "num_ctx": 16384}),
    Sampling("qwen_rec", {"temperature": 0.7, "top_p": 0.8, "top_k": 20,
                          "min_p": 0.0, "num_ctx": 16384}),                      # Alibaba non-thinking rec
    Sampling("near_greedy_ctx", {"temperature": 0.1, "top_p": 0.95, "top_k": 20,
                                 "num_ctx": 16384}),
]


def call(model: str, system: str, user: str, sampling: Sampling,
         client: httpx.Client | None = None, timeout: float = 180.0) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        r = client.post(f"{OLLAMA}/api/chat", json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": sampling.merged(),
        })
        r.raise_for_status()
        d = r.json()
        return {
            "text": (d.get("message", {}) or {}).get("content", "") or "",
            "prompt_tokens": d.get("prompt_eval_count", 0),
            "gen_tokens": d.get("eval_count", 0),
        }
    finally:
        if own:
            client.close()


def parse_json(text: str):
    """Best-effort JSON out of a model reply (handles ```json fences / prose)."""
    t = text.strip()
    if "```" in t:
        t = t.split("```")[1].lstrip("json").strip() if "```json" in t else t.split("```")[1].strip()
    # find first [...] or {...}
    for op, cl in (("[", "]"), ("{", "}")):
        i, j = t.find(op), t.rfind(cl)
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None
