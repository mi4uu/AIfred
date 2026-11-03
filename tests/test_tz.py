"""Europe/Warsaw DST handling (V35), offset must flip CET/CEST per date,
not stay hardcoded at +02:00 (the Nov 2026 bug: CET is +01:00 in winter)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from aifred.tz import get_timezone, offset_for_date, offset_for_datetime, offset_str


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


def test_offset_for_date_at_midnight_is_wrong_for_later_spring_times():
    # Documents why offset_for_date alone isn't enough on a transition day:
    # midnight on 2026-03-29 is still CET, but Warsaw springs forward at 02:00
    # that day, so 09:00 the same day is already CEST.
    assert offset_for_date("2026-03-29") == "+01:00"


def test_offset_for_datetime_spring_forward_day():
    # Warsaw springs forward at 02:00 on 2026-03-29 (CET -> CEST).
    assert offset_for_datetime("2026-03-29", "09:00") == "+02:00"


def test_offset_for_datetime_autumn_fallback_day():
    # Warsaw falls back at 03:00 CEST -> 02:00 CET on 2026-10-25.
    # Midnight-based logic would wrongly keep CEST (+02:00) for the whole day.
    assert offset_for_datetime("2026-10-25", "09:00") == "+01:00"


def test_offset_for_datetime_before_autumn_transition_is_still_cest():
    assert offset_for_datetime("2026-10-25", "01:00") == "+02:00"
