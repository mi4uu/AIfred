"""Secret audit (V8, C5). Scan tree for leaked secrets + check .env perms.

Run `uv run python -m aifred.audit` to scan the repo. CI/dev guard so secrets
never land in code or logs. Skips gitignored secret files themselves (they hold
secrets by design and are not committed).
"""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path

# patterns for common leaked credentials
SECRET_PATTERNS = {
    "google_oauth_token": re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),
    "google_refresh": re.compile(r"1//[A-Za-z0-9_\-]{30,}"),
    "openai_sk": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "openrouter": re.compile(r"sk-or-[A-Za-z0-9\-]{20,}"),
    "aws_akia": re.compile(r"AKIA[0-9A-Z]{16}"),
    "telegram_bot": re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    "bearer_inline": re.compile(r"Bearer\s+[A-Za-z0-9_\-]{20,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
}

# files/dirs never scanned (gitignored secret holders + binaries)
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "data"}
SKIP_FILES = {".env"}
SKIP_SUFFIXES = (".lock", ".pyc")
SECRET_FILE_SUFFIXES = ("_token.json", "_secret.json")


def _should_scan(p: Path) -> bool:
    if p.name in SKIP_FILES:
        return False
    if any(p.name.endswith(s) for s in SECRET_FILE_SUFFIXES):
        return False
    if p.suffix in SKIP_SUFFIXES:
        return False
    return True


def scan_dir(root: str | Path) -> list[tuple[str, str, int]]:
    """Return findings (path, pattern_name, line_no). Empty = clean."""
    root = Path(root)
    findings: list[tuple[str, str, int]] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not _should_scan(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "audit:allow" in line:  # deliberate test fixture / example
                continue
            for name, pat in SECRET_PATTERNS.items():
                if pat.search(line):
                    findings.append((str(p.relative_to(root)), name, i))
    return findings


def env_perms_ok(path: str | Path) -> bool:
    """True if .env is not group/other readable (chmod 600/400). C5."""
    p = Path(path)
    if not p.exists():
        return True  # nothing to leak
    mode = p.stat().st_mode
    return not (mode & (stat.S_IRWXG | stat.S_IRWXO))


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    findings = scan_dir(root)
    env = root / ".env"
    perms_ok = env_perms_ok(env)
    if findings:
        for f, name, ln in findings:
            print(f"LEAK {f}:{ln} matches {name}")
    if not perms_ok:
        print(f"PERMS {env} is group/other accessible — chmod 600")
    if findings or not perms_ok:
        return 1
    print("secret audit clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
