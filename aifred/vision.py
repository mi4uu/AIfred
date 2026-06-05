"""Vision on-demand (V36).

The agent runs on qwen3.6:27b (text + reliable tools). Vision is needed only
sporadically (a forwarded photo, a screenshot), so instead of making the main
model multimodal we call a VL model (qwen3-vl) just for that one image and feed
the resulting TEXT back to the agent. With OLLAMA_MAX_LOADED_MODELS the VL model
loads on demand; for rare use the brief swap is acceptable.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

log = logging.getLogger("aifred.vision")

DEFAULT_PROMPT = (
    "Opisz dokładnie, co jest na obrazku. Jeśli jest tekst — przepisz go WIERNIE "
    "(daty, godziny, kwoty, nazwiska). Nie zgaduj tego, czego nie widać. Zwięźle, po polsku."
)


def _b64(image: bytes | str) -> str:
    if isinstance(image, str):  # already base64 or a path
        p = Path(image)
        if p.exists():
            return base64.b64encode(p.read_bytes()).decode()
        return image
    return base64.b64encode(image).decode()


def describe_image(image: bytes | str, prompt: str = DEFAULT_PROMPT,
                   model: str = "qwen3-vl:8b-instruct-q8_0",
                   base_url: str = "http://localhost:11434",
                   client: httpx.Client | None = None) -> str:
    """Return a text description/OCR of one image via a VL model. Best-effort:
    returns '' on failure so callers degrade gracefully."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    own = client is None
    client = client or httpx.Client(timeout=180.0)
    try:
        r = client.post(f"{base}/api/chat", json={
            "model": model, "stream": False, "think": False, "keep_alive": "5m",
            "messages": [{"role": "user", "content": prompt, "images": [_b64(image)]}],
            "options": {"temperature": 0.0},
        })
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "").strip()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("vision describe failed: %s", e)
        return ""
    finally:
        if own:
            client.close()
