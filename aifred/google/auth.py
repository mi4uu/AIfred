"""Google OAuth (I.gmail, I.calendar, V4).

Scopes: calendar read+write + gmail READ-ONLY (V4, no send scope by default;
sending mail would need a separate explicit scope + confirm, T13/T15).

Hermes token (~/.hermes/google_token.json) has calendar scope only, so first
run needs re-auth via authorize() to add gmail.readonly. Detection: scopes_ok
/ NeedsReauth tells the agent to prompt the user instead of failing silent (V9).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

_OWNER_ONLY = stat.S_IRUSR | stat.S_IWUSR


def _write_secret(path: str | Path, content: str) -> None:
    """Write a token/state file readable by the owner only (contains OAuth secrets).

    Creates the file with mode 0600 from the first open (not a write-then-chmod),
    so there is no window where a permissive umask leaves it world-readable, then
    renames it into place atomically.
    """
    p = Path(path)
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_ONLY)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(p)


def _harden_perms(path: str | Path) -> None:
    """Enforce owner-only perms on a token/state file that already exists,
    e.g. one written before this hardening was added."""
    p = Path(path)
    if p.exists() and stat.S_IMODE(p.stat().st_mode) != _OWNER_ONLY:
        p.chmod(_OWNER_ONLY)

# V4: read-only gmail. Calendar full (manage). Contacts read-only for identity
# (name<->email/phone). No gmail.send here by design.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/drive.readonly",  # read Docs/Sheets links (export), V29
]


class NeedsReauth(Exception):
    """Token missing required scopes, user must run `aifred google auth` (V9)."""


def scopes_ok(creds: Credentials) -> bool:
    have = set(creds.scopes or [])
    return set(SCOPES).issubset(have)


def load_credentials(token_path: str | Path) -> Credentials:
    """Load + refresh creds. Raises NeedsReauth if scopes insufficient (V4)."""
    p = Path(token_path)
    if not p.exists():
        raise NeedsReauth(f"no token at {p}; run authorize()")
    _harden_perms(p)
    info = json.loads(p.read_text())
    # check GRANTED scopes from the token file, not the requested SCOPES
    granted = set(info.get("scopes") or [])
    if not set(SCOPES).issubset(granted):
        raise NeedsReauth(f"token scopes {granted} missing {set(SCOPES) - granted}; re-auth needed")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write_secret(p, creds.to_json())
    return creds


def authorize(client_secret_path: str | Path, token_path: str | Path, port: int = 0) -> Credentials:
    """Interactive install-app OAuth (local browser). Opens browser, writes token.

    Use when running on the machine with a browser. Adds gmail.readonly + calendar.
    For a REMOTE user (different machine) use auth_url()/exchange_code() instead.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=port)
    _write_secret(token_path, creds.to_json())
    return creds


# ---- remote / copy-paste flow (user on a different machine than the agent) ----

REDIRECT_URI = "http://localhost"


def _flow(client_secret_path: str | Path):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_secrets_file(str(client_secret_path), SCOPES, redirect_uri=REDIRECT_URI)


def auth_url(client_secret_path: str | Path, state_path: str | Path) -> str:
    """Return a consent URL. User opens it, approves, gets redirected to
    http://localhost/?code=... (which won't load, that's fine); they copy the
    `code` and feed it to exchange_code(). PKCE verifier persisted to state_path.
    """
    flow = _flow(client_secret_path)
    url, _state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    _write_secret(state_path, json.dumps({"code_verifier": flow.code_verifier}))
    return url


def exchange_code(client_secret_path: str | Path, state_path: str | Path, code_or_url: str, token_path: str | Path) -> Credentials:
    """Exchange the pasted code (or full redirect URL) for a token. Writes token."""
    import os
    from urllib.parse import parse_qs, urlparse

    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # google may reorder/add scopes
    code = code_or_url.strip()
    if code.startswith("http"):
        qs = parse_qs(urlparse(code).query)
        code = (qs.get("code") or [""])[0]
    if not code:
        raise NeedsReauth("no authorization code found in input")

    _harden_perms(state_path)
    state = json.loads(Path(state_path).read_text())
    flow = _flow(client_secret_path)
    flow.code_verifier = state["code_verifier"]
    flow.fetch_token(code=code)
    creds = flow.credentials
    _write_secret(token_path, creds.to_json())
    return creds
