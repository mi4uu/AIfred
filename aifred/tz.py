"""Owner timezone helpers (shared by agent/loop.py and skills/proposals.py).

Uses zoneinfo so the UTC offset is computed per-date (CET +01:00 winter,
CEST +02:00 summer for Europe/Warsaw) instead of a hardcoded constant that
goes wrong half the year.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aifred.config import get_settings


def get_timezone(tz_name: str | None = None) -> ZoneInfo:
    return ZoneInfo(tz_name or get_settings().timezone)


def now_in_tz(tz_name: str | None = None) -> datetime:
    return datetime.now(get_timezone(tz_name))


def offset_str(dt: datetime) -> str:
    """RFC3339 UTC offset for an aware datetime, e.g. '+01:00'."""
    off = dt.strftime("%z")
    return f"{off[:3]}:{off[3:]}"


def offset_for_date(date: str, tz_name: str | None = None) -> str:
    """RFC3339 UTC offset at midnight on calendar date `date` (YYYY-MM-DD) in
    the given (or configured) timezone. For a specific time of day use
    offset_for_datetime instead: the offset can differ from midnight's on a
    DST transition day."""
    local = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=get_timezone(tz_name))
    return offset_str(local)


def offset_for_datetime(date: str, hm: str, tz_name: str | None = None) -> str:
    """RFC3339 UTC offset in effect at `date` `hm` (YYYY-MM-DD, HH:MM) in the
    given (or configured) timezone. Resolves the offset for that exact local
    moment rather than midnight, so it is correct on DST transition days.

    An ambiguous time (autumn fall-back, ticks twice) resolves to the first
    (pre-transition) occurrence; a nonexistent time (spring forward, the
    skipped hour) resolves using the offset that was in effect just before
    the gap. Both follow zoneinfo's default fold=0 behaviour.
    """
    local = datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=get_timezone(tz_name))
    return offset_str(local)
