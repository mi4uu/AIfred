"""brain.md wrapper (I.brainmd, V2, V12) — binds the live MCP RAG tools.

Server tools (discovered live):
  search_notes(query, scope)            full-text, optional folder scope
  similar_notes(query, k, scope)        semantic/RAG top-k chunks
  context_for_query(query, budget_tokens, scope)  token-budgeted context pack
  read_note(path) / write_note(path,content) / append_note(path,content)
  list_notes(folder) / get_tasks(filter) / find_similar_tasks(...)
  current_datetime()                    absolute time orientation

`scope` confines reads/searches to folders/subfolders — used to keep an
internal-reasoning catalog separate from the user-facing journal (the hermes
pattern: think in `internal/`, distill conclusions into `journal/`).

retrieve() uses context_for_query: native token-budgeted chunking, so the LLM
never sees the whole vault (V12) and the payload stays within ctx budget (C7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aifred.mcp.client import McpClient, McpError

DEFAULT_TOP_K = 5
DEFAULT_BUDGET_TOKENS = 1500
CHUNK_MAX_CHARS = 800

_WORD = re.compile(r"[a-z0-9]+")


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Local fallback chunker (used only if server RAG is unavailable)."""
    if not text:
        return []
    sections: list[str] = []
    buf = ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if len(buf) + len(block) + 2 <= max_chars:
            buf = f"{buf}\n\n{block}" if buf else block
        else:
            if buf:
                sections.append(buf)
            if len(block) <= max_chars:
                buf = block
            else:
                for i in range(0, len(block), max_chars):
                    sections.append(block[i : i + max_chars])
                buf = ""
    if buf:
        sections.append(buf)
    return sections


def rank_chunks(chunks: list[str], query: str, top_k: int = DEFAULT_TOP_K) -> list[str]:
    q = set(_WORD.findall(query.lower()))
    if not q:
        return chunks[:top_k]
    scored = []
    for ch in chunks:
        score = sum(1 for w in _WORD.findall(ch.lower()) if w in q)
        if score:
            scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def _as_text(raw) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("text") or raw.get("context") or str(raw)
    return str(raw)


def _scope_arg(scope: str | list[str] | None) -> dict:
    if scope is None:
        return {}
    return {"scope": [scope] if isinstance(scope, str) else list(scope)}


@dataclass
class BrainMD:
    client: McpClient

    # --- time ---
    def now(self) -> str:
        return _as_text(self.client.call_tool("current_datetime", {}))

    # --- search / retrieval (V12) ---
    def context(self, query: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS, scope=None) -> str:
        """Token-budgeted context pack (V12/C7 native)."""
        args = {"query": query, "budget_tokens": budget_tokens, **_scope_arg(scope)}
        return _as_text(self.client.call_tool("context_for_query", args))

    def search(self, query: str, scope=None) -> str:
        return _as_text(self.client.call_tool("search_notes", {"query": query, **_scope_arg(scope)}))

    def similar(self, query: str, k: int = DEFAULT_TOP_K, scope=None) -> str:
        return _as_text(self.client.call_tool("similar_notes", {"query": query, "k": k, **_scope_arg(scope)}))

    def retrieve(self, query: str, top_k: int = DEFAULT_TOP_K, scope=None) -> list[str]:
        """Relevant chunks, never whole vault (V12). Backed by context_for_query."""
        text = self.context(query, budget_tokens=top_k * 300, scope=scope)
        if not text:
            return []
        chunks = chunk_text(text)
        return chunks[:top_k] if chunks else [text]

    # --- notes ---
    def read(self, path: str) -> str:
        raw = _as_text(self.client.call_tool("read_note", {"path": path}))
        # read_note returns a JSON envelope {"path","content","mtime"}; unwrap it
        try:
            import json

            d = json.loads(raw)
            if isinstance(d, dict) and "content" in d:
                return d["content"]
        except (json.JSONDecodeError, TypeError):
            pass
        return raw

    def write(self, path: str, content: str) -> str:
        return _as_text(self.client.call_tool("write_note", {"path": path, "content": content}))

    def append(self, content: str, path: str = "journal/inbox.md") -> str:
        """Append a paragraph to a note (V2 canonical). Default = journal inbox."""
        return _as_text(self.client.call_tool("append_note", {"path": path, "content": content}))

    def list_notes(self, folder: str | None = None) -> str:
        args = {"folder": folder} if folder else {}
        return _as_text(self.client.call_tool("list_notes", args))

    # --- tasks ---
    def tasks(self, filter: str = "open") -> str:
        return _as_text(self.client.call_tool("get_tasks", {"filter": filter}))

    def similar_tasks(self, query: str, k: int = DEFAULT_TOP_K, filter: str = "open", scope=None) -> str:
        return _as_text(
            self.client.call_tool("find_similar_tasks", {"query": query, "k": k, "filter": filter, **_scope_arg(scope)})
        )

    def connect(self) -> "BrainMD":
        self.client.initialize()
        return self
