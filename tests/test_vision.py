"""Vision on-demand (V36) — describe_image via VL model + last_image lookup."""

import base64

import httpx

from aifred.store.db import Store
from aifred.vision import describe_image


def test_describe_image_sends_image_and_returns_text():
    captured = {}

    def handler(req):
        body = req.read().decode()
        captured["has_image"] = "images" in body
        return httpx.Response(200, json={"message": {"content": "DENTYSTA 16:40 Zosia"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = describe_image(b"\x89PNGfake", model="qwen3-vl:8b", client=client)
    assert out == "DENTYSTA 16:40 Zosia"
    assert captured["has_image"]


def test_describe_image_degrades_on_error():
    def handler(req):
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert describe_image(b"x", client=client) == ""  # best-effort, empty on failure


def test_describe_image_accepts_base64_string():
    def handler(req):
        return httpx.Response(200, json={"message": {"content": "ok"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    b64 = base64.b64encode(b"img").decode()
    assert describe_image(b64, client=client) == "ok"


def test_last_image_lookup():
    s = Store(":memory:")
    s.add_message("whatsapp", "c1", "t1", ts=1.0, body="zwykły tekst", sender="x")
    s.add_message("whatsapp", "c1", "img1", ts=2.0, body="[obraz] plan zajęć", sender="x")
    s.add_message("whatsapp", "c1", "t2", ts=3.0, body="kolejny tekst", sender="x")
    img = s.last_image()
    assert img and img["ext_id"] == "img1" and img["body"].startswith("[obraz]")
    s.close()


def test_last_image_none_when_no_images():
    s = Store(":memory:")
    s.add_message("whatsapp", "c1", "t1", ts=1.0, body="tekst", sender="x")
    assert s.last_image() is None
    s.close()
