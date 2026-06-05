"""Read Google Docs / Sheets / Slides links (V29).

When a message or self-note contains a Google link, AIfred fetches the actual
content via the Drive *export* endpoint (needs only drive.readonly — no separate
Sheets/Docs scope): Sheets -> CSV, Docs/Slides -> plain text. The text is then
available to the agent (gdoc_read tool) and to the self-note event extractor, so
events come from the real schedule, not just the chat line that linked it.
"""

from __future__ import annotations

import io
import re

# docs.google.com/<kind>/d/<id>  +  drive.google.com/file/d/<id>
_DOCS = re.compile(r"docs\.google\.com/(spreadsheets|document|presentation)/d/([\w-]+)")
_DRIVE = re.compile(r"drive\.google\.com/file/d/([\w-]+)")

_EXPORT = {
    "spreadsheets": "text/csv",
    "document": "text/plain",
    "presentation": "text/plain",
}
MAX_CHARS = 4000  # keep retrieved content small — big context hurts (eval/V28)


def find_links(text: str) -> list[tuple[str, str]]:
    """Return [(kind, file_id)] for every Google Docs/Drive link in text."""
    out = [(m.group(1), m.group(2)) for m in _DOCS.finditer(text or "")]
    out += [("file", m.group(1)) for m in _DRIVE.finditer(text or "")]
    # dedup, keep order
    seen, uniq = set(), []
    for kind, fid in out:
        if fid not in seen:
            seen.add(fid)
            uniq.append((kind, fid))
    return uniq


def _download(req) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def read_file(drive, kind: str, file_id: str, max_chars: int = MAX_CHARS) -> dict:
    """Fetch one file's text via Drive export. Returns {ok, title, kind, text}."""
    try:
        meta = drive.files().get(fileId=file_id, fields="name,mimeType").execute()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "file_id": file_id, "error": str(e)[:160]}
    name = meta.get("name", file_id)
    mime = meta.get("mimeType", "")
    try:
        if kind in _EXPORT and mime.startswith("application/vnd.google-apps"):
            req = drive.files().export_media(fileId=file_id, mimeType=_EXPORT[kind])
        else:  # plain uploaded file
            req = drive.files().get_media(fileId=file_id)
        text = _download(req).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "file_id": file_id, "title": name, "error": str(e)[:160]}
    truncated = len(text) > max_chars
    return {"ok": True, "file_id": file_id, "title": name, "kind": kind,
            "text": text[:max_chars], "truncated": truncated}


def read_url(drive, url: str, max_chars: int = MAX_CHARS) -> dict:
    links = find_links(url)
    if not links:
        return {"ok": False, "error": "no Google Docs/Drive link found"}
    kind, fid = links[0]
    return read_file(drive, kind, fid, max_chars)


def fetch_all(drive, text: str, max_chars: int = MAX_CHARS) -> list[dict]:
    """Read every Google link found in text (best-effort; skips failures)."""
    out = []
    for kind, fid in find_links(text):
        r = read_file(drive, kind, fid, max_chars)
        if r.get("ok"):
            out.append(r)
    return out
