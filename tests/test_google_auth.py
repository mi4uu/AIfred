"""T7 google auth tests. No network — scope gating only (V4, V9)."""

import json

import pytest

from aifred.google.auth import SCOPES, NeedsReauth, load_credentials, scopes_ok


class FakeCreds:
    def __init__(self, scopes):
        self.scopes = scopes
        self.expired = False
        self.refresh_token = None


def test_scopes_ok_requires_gmail_readonly():
    assert scopes_ok(FakeCreds(SCOPES)) is True
    # hermes token had calendar only -> not ok (V4 gap)
    assert scopes_ok(FakeCreds(["https://www.googleapis.com/auth/calendar"])) is False


def test_load_missing_token_raises(tmp_path):
    with pytest.raises(NeedsReauth):
        load_credentials(tmp_path / "nope.json")


def test_load_insufficient_scope_raises(tmp_path):
    # simulate hermes calendar-only token -> NeedsReauth, not silent (V9)
    tok = tmp_path / "token.json"
    tok.write_text(
        json.dumps(
            {
                "token": "x",
                "refresh_token": "y",
                "client_id": "c",
                "client_secret": "s",
                "scopes": ["https://www.googleapis.com/auth/calendar"],
            }
        )
    )
    with pytest.raises(NeedsReauth):
        load_credentials(tok)


def test_gmail_scope_is_readonly_not_send():
    # V4: read-only by default, no send scope baked in
    assert any("gmail.readonly" in s for s in SCOPES)
    assert not any("gmail.send" in s for s in SCOPES)
