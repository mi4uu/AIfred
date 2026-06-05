"""T1 app tests. Health + redacted /config (I.web skeleton)."""

from fastapi.testclient import TestClient

from aifred.main import create_app


def test_health():
    c = TestClient(create_app())
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_config_endpoint_redacted(monkeypatch):
    # V8: /config must not leak secret values
    monkeypatch.setenv("MCP_BRAIN_MD_API_KEY", "leakme")
    from aifred.config import get_settings

    get_settings.cache_clear()
    c = TestClient(create_app())
    r = c.get("/config")
    assert r.status_code == 200
    assert "leakme" not in r.text
    get_settings.cache_clear()
