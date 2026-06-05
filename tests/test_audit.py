"""T18 secret audit tests (V8, C5)."""

import os
import stat
from pathlib import Path

from aifred.audit import env_perms_ok, scan_dir


def test_detects_planted_secret(tmp_path):
    (tmp_path / "leak.py").write_text('TOKEN = "ya29.abcdefghijklmnopqrstuvwxyz123456"\n')  # audit:allow fixture
    findings = scan_dir(tmp_path)
    assert any(name == "google_oauth_token" for _, name, _ in findings)


def test_skips_gitignored_secret_files(tmp_path):
    # *_token.json holds secrets by design and is gitignored -> not flagged
    (tmp_path / "google_token.json").write_text('{"refresh_token": "1//abc"}\n')
    (tmp_path / ".env").write_text("MCP_BRAIN_MD_API_KEY=Bearer secretsecretsecretsecret\n")  # audit:allow fixture
    assert scan_dir(tmp_path) == []


def test_clean_dir(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n")
    assert scan_dir(tmp_path) == []


def test_env_perms(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SECRET=x")
    os.chmod(env, stat.S_IRUSR | stat.S_IWUSR)  # 600
    assert env_perms_ok(env) is True
    os.chmod(env, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)  # world-readable
    assert env_perms_ok(env) is False


def test_repo_is_clean():
    # V8: the actual AIfred repo must have no committed secrets
    root = Path(__file__).resolve().parent.parent
    assert scan_dir(root) == []
