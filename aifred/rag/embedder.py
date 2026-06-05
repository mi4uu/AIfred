"""Standalone embedder (V31) — ollama /api/embed, NOT the chat path.

Runs a tiny embedding model (qwen3-embedding:0.6b) that, with
OLLAMA_MAX_LOADED_MODELS=2, stays resident ALONGSIDE the chat model — so a
recall() never evicts qwen3-vl. Vectors are stored as float32 bytes; cosine is
pure-python (corpus is small — hundreds/thousands of snippets), so no numpy dep.
"""

from __future__ import annotations

import math
from array import array

import httpx


def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def cosine(a, b) -> float:
    s = na = nb = 0.0
    for x, y in zip(a, b):
        s += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return s / (math.sqrt(na) * math.sqrt(nb))


class Embedder:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen3-embedding:0.6b",
                 keep_alive: str = "30m", client: httpx.Client | None = None):
        self.base = base_url.rstrip("/")
        # tolerate an OpenAI-style base ("…/v1") — embed lives on the native root
        if self.base.endswith("/v1"):
            self.base = self.base[: -len("/v1")]
        self.model = model
        self.keep_alive = keep_alive
        self._client = client or httpx.Client(timeout=180.0)  # first call cold-loads the embed model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = self._client.post(f"{self.base}/api/embed",
                              json={"model": self.model, "input": texts, "keep_alive": self.keep_alive})
        r.raise_for_status()
        return r.json().get("embeddings", [])

    def embed_one(self, text: str) -> list[float]:
        out = self.embed([text])
        return out[0] if out else []
