"""T2 LLM client tests. MockTransport — no live ollama needed.

Covers parse, budget-before-send (C7), cloud fallback opt-in gating (V1/C6).
"""

import httpx

from aifred.config import Settings
from aifred.llm.client import LLMClient


def _ollama_native(content="hi", tool_calls=None):
    # ollama native /api/chat shape
    return {"message": {"role": "assistant", "content": content, "tool_calls": tool_calls or []}}


def _openrouter_response(content="hi"):
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def test_chat_parses_ollama():
    def handler(req):
        assert req.url.path.endswith("/api/chat")  # native endpoint, not /v1
        body = httpx.Request("POST", req.url, content=req.content).read()
        assert b'"think": false' in body or b'"think":false' in body  # think disabled for speed
        return httpx.Response(200, json=_ollama_native("pong"))

    s = Settings(_env_file=None)
    c = LLMClient(s, client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = c.chat([{"role": "user", "content": "ping"}])
    assert r.content == "pong"
    assert r.provider == "ollama"


def test_native_tool_calls_dict_args():
    # native ollama returns arguments as a dict (not JSON string)
    tc = [{"function": {"name": "x", "arguments": {"a": 1}}}]

    def handler(req):
        return httpx.Response(200, json=_ollama_native("", tc))

    s = Settings(_env_file=None)
    c = LLMClient(s, client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = c.chat([{"role": "user", "content": "ping"}])
    assert r.tool_calls[0]["function"]["arguments"] == {"a": 1}


def test_no_fallback_when_disabled():
    # V1/C6: ollama down + fallback disabled => raise, never hit cloud
    def handler(req):
        return httpx.Response(500)

    s = Settings(_env_file=None, AIFRED_CLOUD_FALLBACK_ENABLED=False)
    c = LLMClient(s, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        c.chat([{"role": "user", "content": "ping"}])
        assert False, "should have raised"
    except httpx.HTTPError:
        pass


def test_fallback_when_enabled():
    hits = {"ollama": 0, "openrouter": 0}

    def handler(req):
        if "openrouter" in req.url.host:
            hits["openrouter"] += 1
            assert req.headers["authorization"] == "Bearer key123"
            return httpx.Response(200, json=_openrouter_response("cloud"))
        hits["ollama"] += 1
        return httpx.Response(500)

    s = Settings(
        _env_file=None,
        AIFRED_CLOUD_FALLBACK_ENABLED=True,
        OPENROUTER_API_KEY="key123",
    )
    c = LLMClient(s, client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = c.chat([{"role": "user", "content": "ping"}])
    assert r.content == "cloud"
    assert r.provider == "openrouter"
    assert hits["ollama"] == 1 and hits["openrouter"] == 1
