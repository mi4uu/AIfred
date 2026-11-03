"""T7 google auth tests. No network, scope gating only (V4, V9)."""

import json
import os
import stat

import pytest

from aifred.google.auth import SCOPES, NeedsReauth, _harden_perms, _write_secret, load_credentials, scopes_ok


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


def test_write_secret_sets_owner_only_permissions(tmp_path):
    p = tmp_path / "token.json"
    _write_secret(p, '{"token": "x"}')
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR
    assert p.read_text() == '{"token": "x"}'


def test_write_secret_creates_file_owner_only_even_with_permissive_umask(tmp_path):
    # The file must be born 0600, not written world-readable then chmod'd
    # after the fact. A permissive umask (022, or even 0) must not widen it,
    # because os.open's explicit mode wins over write_text's default mode.
    p = tmp_path / "token.json"
    old_umask = os.umask(0o000)
    try:
        _write_secret(p, '{"token": "x"}')
    finally:
        os.umask(old_umask)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_write_secret_leaves_no_tmp_file_behind(tmp_path):
    p = tmp_path / "token.json"
    _write_secret(p, "{}")
    assert sorted(tmp_path.iterdir()) == [p]


def test_harden_perms_fixes_a_preexisting_permissive_token(tmp_path):
    p = tmp_path / "token.json"
    p.write_text('{"token": "x"}')
    p.chmod(0o644)  # simulate a token written before hardening existed
    _harden_perms(p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_load_credentials_hardens_existing_token_perms(tmp_path):
    tok = tmp_path / "token.json"
    tok.write_text(
        json.dumps(
            {
                "token": "x",
                "refresh_token": "y",
                "client_id": "c",
                "client_secret": "s",
                "scopes": [],
            }
        )
    )
    tok.chmod(0o644)
    with pytest.raises(NeedsReauth):
        load_credentials(tok)  # insufficient scopes, but perms must be hardened first
    mode = stat.S_IMODE(tok.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR
