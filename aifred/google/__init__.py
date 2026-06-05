"""Google integration — OAuth (T7) + gmail/calendar tools (T8)."""

from aifred.google.auth import (
    SCOPES,
    NeedsReauth,
    authorize,
    load_credentials,
    scopes_ok,
)

__all__ = ["SCOPES", "NeedsReauth", "authorize", "load_credentials", "scopes_ok"]
