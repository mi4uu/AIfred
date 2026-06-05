"""Intent router (V14, V15). Code-based keyword → tags → tool subset.

Deterministic, no LLM call (V15). Keeps per-turn tool schemas small (C7): the
model never sees every tool, only ones whose tags match the message plus the
always-on "core" tools. Unmatched message falls back to core only.
"""

from __future__ import annotations

import re

from aifred.tools.registry import ToolRegistry

# keyword (substring/word) -> tags it activates
KEYWORD_TAGS: dict[str, set[str]] = {
    "mail": {"gmail"},
    "email": {"gmail"},
    "gmail": {"gmail"},
    "inbox": {"gmail"},
    "calendar": {"calendar"},
    "event": {"calendar"},
    "meeting": {"calendar"},
    "schedule": {"calendar"},
    "remind": {"calendar"},
    "whatsapp": {"whatsapp"},
    "group": {"whatsapp"},
    "message": {"whatsapp"},
    "journal": {"journal"},
    "note": {"journal"},
    "log": {"journal"},
    "diary": {"journal"},
    "todo": {"tasks"},
    "task": {"tasks"},
    "done": {"tasks"},
    "digest": {"digest"},
    "summary": {"digest"},
    "brief": {"digest"},
    # importance / "what's up today" — surface attention + calendar + whatsapp
    "important": {"digest", "calendar", "whatsapp"},
    "ważne": {"digest", "calendar", "whatsapp"},
    "wazne": {"digest", "calendar", "whatsapp"},
    "pilne": {"digest", "whatsapp"},
    "dziś": {"digest", "calendar"},
    "dzis": {"digest", "calendar"},
    "dzisiaj": {"digest", "calendar"},
    "today": {"digest", "calendar"},
}

ALWAYS_ON = "core"


class IntentRouter:
    def __init__(self, registry: ToolRegistry, keyword_tags: dict[str, set[str]] | None = None):
        self.registry = registry
        self.keyword_tags = keyword_tags or KEYWORD_TAGS

    def tags_for(self, message: str) -> set[str]:
        words = set(re.findall(r"\w+", message.lower(), re.UNICODE))  # \w keeps Polish letters
        tags: set[str] = {ALWAYS_ON}
        for kw, t in self.keyword_tags.items():
            # prefix match so plurals/inflections hit ("events" -> "event")
            if any(w == kw or w.startswith(kw) for w in words):
                tags |= t
        return tags

    def select(self, message: str) -> list[str]:
        """Tool names whose tags intersect the message's tags (V14)."""
        wanted = self.tags_for(message)
        out = []
        for name in self.registry.names():
            tool = self.registry.get(name)
            if set(tool.tags) & wanted:
                out.append(name)
        return out
