"""WhatsApp pairing manager + web endpoints (UI-driven pairing)."""

from fastapi.testclient import TestClient

from aifred.confirm import ConfirmManager
from aifred.main import create_app
from aifred.store.db import Store
from aifred.whatsapp.pairing import WhatsAppManager, qr_to_data_url


class FakeAgent:
    def run(self, message, history=None):
        return {"reply": "ok"}


def test_qr_to_data_url():
    url = qr_to_data_url("2@abc,def,ghi")
    assert url.startswith("data:image/png;base64,")
    assert len(url) > 100


def test_manager_status_idle(tmp_path):
    store = Store(":memory:")
    m = WhatsAppManager(store=store, session_path=str(tmp_path / "wa.sqlite"), lock_path=str(tmp_path / "wa.lock"))
    s = m.status()
    assert s["state"] == "idle" and s["paired"] is False and s["qr"] is None
    store.close()


def test_endpoints_status_and_start(tmp_path, monkeypatch):
    store = Store(":memory:")
    m = WhatsAppManager(store=store, session_path=str(tmp_path / "wa.sqlite"), lock_path=str(tmp_path / "wa.lock"))
    # don't actually connect to whatsmeow in the test
    monkeypatch.setattr(m, "start", lambda: {"state": "pairing", "qr": None, "paired": False, "error": None})
    app = create_app(agent=FakeAgent(), confirm=ConfirmManager(), whatsapp=m)
    c = TestClient(app)
    assert c.get("/api/whatsapp/status").json()["state"] == "idle"
    assert c.post("/api/whatsapp/start").json()["state"] == "pairing"
    store.close()


def test_endpoints_unavailable_without_manager():
    app = create_app(agent=FakeAgent(), confirm=ConfirmManager())  # no whatsapp
    c = TestClient(app)
    assert c.get("/api/whatsapp/status").json()["state"] == "unavailable"
    assert c.post("/api/whatsapp/start").status_code == 503
