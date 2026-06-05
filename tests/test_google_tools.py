"""T8 google tools tests. Fake services, no network.

Covers gmail read shape (V4), body truncation (V11), calendar source req (V3),
side-effecting flags (V7), compact schema (V10).
"""

import pytest

from aifred.google.tools import (
    BODY_MAX,
    build_google_tools,
    calendar_create,
    gmail_get,
    gmail_search,
)
from aifred.tools.base import ToolError


class _Exec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        return self._r


class FakeMessages:
    def __init__(self, listing, msgs):
        self._listing = listing
        self._msgs = msgs

    def list(self, **kw):
        return _Exec(self._listing)

    def get(self, **kw):
        return _Exec(self._msgs[kw["id"]])


class FakeGmail:
    def __init__(self, listing, msgs):
        self._m = FakeMessages(listing, msgs)

    def users(self):
        return self

    def messages(self):
        return self._m


class FakeEvents:
    def __init__(self):
        self.inserted = None

    def list(self, **kw):
        return _Exec({"items": [{"id": "e1", "summary": "standup", "start": {"dateTime": "x"}, "end": {"dateTime": "y"}}]})

    def insert(self, calendarId, body):
        self.inserted = body
        return _Exec({"id": "new1", "summary": body["summary"], "htmlLink": "http://x"})


class FakeCal:
    def __init__(self):
        self._e = FakeEvents()

    def events(self):
        return self._e


def _gmail():
    return FakeGmail(
        listing={"messages": [{"id": "m1"}]},
        msgs={
            "m1": {
                "id": "m1",
                "snippet": "hello",
                "payload": {"headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "a@b"}]},
            }
        },
    )


def test_gmail_search_shape():
    out = gmail_search(_gmail(), "is:unread")
    assert out[0]["subject"] == "Hi" and out[0]["from"] == "a@b"


def test_gmail_get_truncates_body():
    import base64

    big = base64.urlsafe_b64encode(b"z" * (BODY_MAX + 500)).decode()
    g = FakeGmail({}, {"m1": {"id": "m1", "payload": {"body": {"data": big}, "headers": []}}})
    out = gmail_get(g, "m1")
    assert len(out["body"]) == BODY_MAX  # V11


def test_calendar_create_requires_source():
    with pytest.raises(ToolError):
        calendar_create(FakeCal(), "x", "s", "e", source="")  # V3


def test_calendar_create_records_source():
    cal = FakeCal()
    calendar_create(cal, "lunch", "s", "e", source="brain.md#plans")
    assert "brain.md#plans" in cal.events().inserted["description"]  # V3 provenance


def test_build_tools_flags_and_schema():
    tools = {t.name: t for t in build_google_tools(_gmail(), FakeCal())}
    assert tools["gmail_search"].side_effecting is False  # V4 read-only
    assert tools["calendar_create"].side_effecting is True  # V7 gated
    sch = tools["gmail_search"].schema()
    assert "title" not in sch["function"]["parameters"]  # V10 compact
