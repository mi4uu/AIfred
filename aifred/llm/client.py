"""LLM client. Ollama local first (I.model, C1). Cloud fallback opt-in (V1, C6).

Uses ollama's NATIVE /api/chat (not /v1) so we can set think=false — qwen3.6's
reasoning tokens otherwise dominate latency (16s vs 6s) and cause cloudflare
502s. Budget guard applied before every send (C7). httpx.Client injectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from aifred.config import Settings, get_settings
from aifred.llm.budget import BudgetGuard

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    model: str = ""
    provider: str = "ollama"
    raw: dict[str, Any] = field(default_factory=dict)


def _native_base(url: str) -> str:
    u = url.rstrip("/")
    return u[: -len("/v1")] if u.endswith("/v1") else u


class LLMClient:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.s = settings or get_settings()
        self._client = client or httpx.Client(timeout=180.0)
        self.guard = BudgetGuard(self.s.ctx_budget)

    def _options(self, temperature: float) -> dict[str, Any]:
        """Sampling options (V28). num_ctx is critical — ollama's runtime default is
        4096 and silently truncates longer prompts, which looks like the model
        'losing context'. Tuned values come from aifred/eval."""
        opts: dict[str, Any] = {
            "temperature": temperature if temperature is not None else self.s.temperature,
            "num_ctx": self.s.num_ctx,
        }
        if self.s.top_p:
            opts["top_p"] = self.s.top_p
        if self.s.top_k:
            opts["top_k"] = self.s.top_k
        if self.s.repeat_penalty:
            opts["repeat_penalty"] = self.s.repeat_penalty
        if self.s.min_p:
            opts["min_p"] = self.s.min_p
        return opts

    # ---- ollama native /api/chat ----
    def _ollama_chat(self, messages: list[dict], tools: list[dict] | None, temperature: float) -> dict:
        payload: dict[str, Any] = {
            "model": self.s.model,
            "messages": messages,
            "stream": False,
            "think": self.s.chat_think,
            "keep_alive": self.s.ollama_keep_alive,
            "options": self._options(temperature),
        }
        if tools:
            payload["tools"] = tools
        url = _native_base(self.s.ollama_base_url) + "/api/chat"
        r = self._client.post(url, json=payload)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _parse_native(data: dict, model: str) -> ChatResult:
        msg = data.get("message", {}) or {}
        return ChatResult(
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls") or [],  # native: arguments is a dict
            model=model,
            provider="ollama",
            raw=data,
        )

    # ---- openrouter (OpenAI-compat fallback) ----
    def _openrouter_chat(self, messages: list[dict], tools: list[dict] | None, temperature: float) -> ChatResult:
        payload: dict[str, Any] = {"model": self.s.model, "messages": messages, "temperature": temperature, "stream": False}
        if tools:
            payload["tools"] = tools
        r = self._client.post(
            OPENROUTER_BASE + "/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.s.openrouter_api_key}"},
        )
        r.raise_for_status()
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        return ChatResult(
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls") or [],
            model=self.s.model,
            provider="openrouter",
            raw=data,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None, temperature: float | None = None) -> ChatResult:
        messages = self.guard.trim(messages)  # C7/V11
        temp = self.s.temperature if temperature is None else temperature
        try:
            data = self._ollama_chat(messages, tools, temp)
            return self._parse_native(data, self.s.model)
        except (httpx.HTTPError, httpx.TransportError):
            if not (self.s.cloud_fallback_enabled and self.s.openrouter_api_key):  # V1/C6
                raise
            return self._openrouter_chat(messages, tools, temp)

    def warmup(self) -> bool:
        """Preload the model + refresh keep_alive so chats don't cold-load (502s)."""
        base = _native_base(self.s.ollama_base_url)
        try:
            r = self._client.post(
                f"{base}/api/generate",
                json={
                    "model": self.s.model,
                    "prompt": "hi",
                    "stream": False,
                    "keep_alive": self.s.ollama_keep_alive,
                    "options": {"num_predict": 1, "num_ctx": self.s.num_ctx},
                },
                timeout=180.0,
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False
