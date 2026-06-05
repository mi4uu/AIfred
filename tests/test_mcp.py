"""T6 MCP client + brain.md retrieval tests. MockTransport, no live server.

Covers JSON-RPC flow, SSE parse, session header, and V12 chunked retrieval.
"""

import json

import httpx
import pytest

from aifred.mcp.brainmd import BrainMD, chunk_text, rank_chunks
from aifred.mcp.client import McpClient


def _rpc_ok(req_body, result, session=None):
    headers = {"content-type": "application/json"}
    if session:
        headers["mcp-session-id"] = session
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": req_body.get("id"), "result": result}, headers=headers)


def make_server(tools, tool_results):
    """Fake MCP server over MockTransport."""

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        method = body["method"]
        if method == "initialize":
            return _rpc_ok(body, {"protocolVersion": "x", "serverInfo": {"name": "brain"}}, session="sess1")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            assert req.headers.get("mcp-session-id") == "sess1"  # session propagated
            return _rpc_ok(body, {"tools": tools})
        if method == "tools/call":
            name = body["params"]["name"]
            text = tool_results[name]
            return _rpc_ok(body, {"content": [{"type": "text", "text": text}]})
        return httpx.Response(400, text=f"unexpected {method}")

    return handler


def test_list_and_call_tools():
    handler = make_server(
        tools=[{"name": "brain_search"}, {"name": "brain_append"}],
        tool_results={"brain_search": "hit one\n\nhit two"},
    )
    c = McpClient("https://x/mcp", client=httpx.Client(transport=httpx.MockTransport(handler)))
    names = [t["name"] for t in c.list_tools()]
    assert names == ["brain_search", "brain_append"]
    assert c.call_tool("brain_search", {"query": "q"}) == "hit one\n\nhit two"
    assert c.session_id == "sess1"


def test_sse_response_parsed():
    def handler(req):
        body = json.loads(req.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        payload = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {"ok": True}})
        return httpx.Response(200, text=f"event: message\ndata: {payload}\n\n", headers={"content-type": "text/event-stream"})

    c = McpClient("https://x/mcp", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.initialize()["ok"] is True


def test_chunk_and_rank():
    text = "# A\n\nalpha beta\n\n# B\n\ngamma delta\n\n# C\n\nalpha gamma"
    chunks = chunk_text(text, max_chars=20)
    assert len(chunks) >= 3
    top = rank_chunks(chunks, "alpha", top_k=2)
    assert all("alpha" in c for c in top)


def test_brainmd_retrieve_uses_context_for_query():
    seen = {}

    def handler(req):
        body = json.loads(req.content)
        method = body["method"]
        if method == "initialize":
            return _rpc_ok(body, {"protocolVersion": "x"}, session="s")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            seen["name"] = body["params"]["name"]
            seen["args"] = body["params"]["arguments"]
            return _rpc_ok(body, {"content": [{"type": "text", "text": "para one alpha\n\npara two beta"}]})
        return httpx.Response(400)

    c = McpClient("https://x/mcp", client=httpx.Client(transport=httpx.MockTransport(handler)))
    b = BrainMD(client=c)
    out = b.retrieve("alpha", top_k=5, scope=["Journal"])
    assert seen["name"] == "context_for_query"  # uses native RAG (V12)
    assert seen["args"]["scope"] == ["Journal"]  # folder scope passed
    assert isinstance(out, list) and out  # chunked list, not whole vault (V12)


def test_brainmd_append_targets_path():
    seen = {}

    def handler(req):
        body = json.loads(req.content)
        method = body["method"]
        if method == "initialize":
            return _rpc_ok(body, {}, session="s")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            seen["name"] = body["params"]["name"]
            seen["args"] = body["params"]["arguments"]
            return _rpc_ok(body, {"content": [{"type": "text", "text": "{\"ok\":true}"}]})
        return httpx.Response(400)

    c = McpClient("https://x/mcp", client=httpx.Client(transport=httpx.MockTransport(handler)))
    b = BrainMD(client=c)
    b.append("hello", path="Journal/2026-06-03.md")
    assert seen["name"] == "append_note"
    assert seen["args"] == {"path": "Journal/2026-06-03.md", "content": "hello"}
