"""Context budget guard (C7, V11). Keep turn ctx <=16k.

Approx token count (chars/4 + per-msg overhead) — cheap, no tokenizer dep.
Guard trims oldest non-system messages until fit. System prompt + last user
message always kept. Raises if even minimal set overflows.
"""

from __future__ import annotations

from dataclasses import dataclass

CHARS_PER_TOKEN = 4
MSG_OVERHEAD = 4  # role/format framing per message


def count_tokens(text: str) -> int:
    return (len(text) // CHARS_PER_TOKEN) + MSG_OVERHEAD


def count_messages(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        total += count_tokens(content)
    return total


class BudgetExceeded(Exception):
    """Minimal message set still over budget — caller must shrink data (V11)."""


@dataclass
class BudgetGuard:
    limit: int

    def fits(self, messages: list[dict]) -> bool:
        return count_messages(messages) <= self.limit

    def trim(self, messages: list[dict]) -> list[dict]:
        """Drop oldest middle messages until fit. Keep system + last message.

        Code-side context discipline: never silently send >limit (C7).
        """
        if self.fits(messages):
            return messages
        if not messages:
            return messages

        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        if not rest:
            kept = system
            if not BudgetGuard(self.limit).fits(kept):
                raise BudgetExceeded(f"system prompt alone {count_messages(kept)} > {self.limit}")
            return kept

        last = rest[-1]
        middle = rest[:-1]
        # drop from oldest middle inward
        while middle and not self.fits(system + middle + [last]):
            middle.pop(0)
        kept = system + middle + [last]
        if not self.fits(kept):
            raise BudgetExceeded(
                f"minimal set {count_messages(kept)} > {self.limit}; shrink data before LLM (V11)"
            )
        return kept
