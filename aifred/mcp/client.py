"""Minimal MCP client over streamable HTTP (JSON-RPC 2.0).

Speaks the subset AIfred needs: initialize, tools/list, tools/call. Handles
both plain JSON and SSE (text/event-stream) responses, and the Mcp-Session-Id
header. httpx.Client injectable for tests (no live server needed).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"


class McpError(Exception):
    """MCP transport or JSON-RPC error — surfaced, never swallowed (V9)."""


def _parse_response(resp: httpx.Response) -> dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        # take the last data: line carrying a JSON-RPC object
        last: dict[str, Any] | None = None
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if payload and payload != "[DONE]":
                    try:
                        last = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
        if last is None:
            raise McpError("no JSON-RPC payload in SSE stream")
        return last
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise McpError(f"bad JSON response: {e}") from e


class McpClient:
    def __init__(self, url: str, headers: dict[str, str] | None = None, client: httpx.Client | None = None):
        self.url = url
        self.base_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        self._client = client or httpx.Client(timeout=60.0)
        self._id = 0
        self.session_id: str | None = None
        self._initialized = False

    def _rpc(self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False) -> dict[str, Any] | None:
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self._id
        headers = dict(self.base_headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        resp = self._client.post(self.url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise McpError(f"{method} HTTP {resp.status_code}: {resp.text[:200]}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if notify:
            return None
        data = _parse_response(resp)
        if "error" in data:
            raise McpError(f"{method} rpc error: {data['error']}")
        return data.get("result", {})

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aifred", "version": "0.1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True
        return result or {}

    def _ensure(self) -> None:
        if not self._initialized:
            self.initialize()

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure()
        result = self._rpc("tools/list", {}) or {}
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self._ensure()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}) or {}
        # MCP returns content blocks; collapse text blocks to a string
        content = result.get("content", [])
        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
        if texts:
            return "\n".join(texts)
        return result.get("structuredContent", result)
