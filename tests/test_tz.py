"""Europe/Warsaw DST handling (V35), offset must flip CET/CEST per date,
not stay hardcoded at +02:00 (the Nov 2026 bug: CET is +01:00 in winter)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from aifred.tz import get_timezone, offset_for_date, offset_str


def test_offset_for_date_winter_is_cet():
    assert offset_for_date("2026-01-15") == "+01:00"


def test_offset_for_date_summer_is_cest():
    assert offset_for_date("2026-07-15") == "+02:00"


def test_offset_for_date_respects_explicit_tz_name():
    assert offset_for_date("2026-01-15", "UTC") == "+00:00"


def test_offset_str_matches_zoneinfo_utcoffset():
    dt = datetime(2026, 11, 3, 12, 0, tzinfo=get_timezone("Europe/Warsaw"))
    assert offset_str(dt) == "+01:00"


def test_get_timezone_defaults_to_europe_warsaw():
    assert get_timezone() == ZoneInfo("Europe/Warsaw")
