"""Assembler tests, graceful degrade when services absent."""

from fastapi.testclient import TestClient

from aifred.app import build_runtime
from aifred.config import Settings
from aifred.main import create_app


def test_build_runtime_degrades_without_services(tmp_path):
    s = Settings(
        _env_file=None,
        AIFRED_DATA_DIR=str(tmp_path),
        AIFRED_BRAINMD_MCP_URL="",
        MCP_BRAIN_MD_API_KEY="",
        AIFRED_TELEGRAM_BOT_TOKEN="",
        AIFRED_GOOGLE_TOKEN_PATH="",
    )
    rt = build_runtime(s)
    # nothing configured -> no crash, status explains each subsystem
    assert rt.status["brain"] == "not configured"
    assert rt.status["telegram"] == "no token"
    assert "google" in rt.status
    assert rt.bot is None and rt.brain is None
    # store-only tools always register (store always present); brain/google absent
    assert set(rt.registry.names()) == {
        "whatsapp_recent", "whatsapp_chats", "attention_list", "triage_rule", "triage_rules_list",
        "recall", "vision_describe"
    }
    rt.store.close()


def test_build_runtime_telegram_only(tmp_path):
    s = Settings(
        _env_file=None,
        AIFRED_DATA_DIR=str(tmp_path),
        AIFRED_BRAINMD_MCP_URL="",
        MCP_BRAIN_MD_API_KEY="",
        AIFRED_TELEGRAM_BOT_TOKEN="123:abc",
        AIFRED_TELEGRAM_ALLOWED_USERS="111,222",
        AIFRED_GOOGLE_TOKEN_PATH="",
    )
    rt = build_runtime(s)
    assert rt.bot is not None
    assert rt.bot.allowed_users == {111, 222}
    rt.store.close()


def test_run_service_entrypoint_wires_a_working_chat_route(tmp_path, monkeypatch):
    """`uv run aifred-service` must produce an app whose /api/chat route is
    live, not the bare create_app() used by `uv run aifred` (no agent), and
    that route must actually answer, not just be present in app.routes. This
    guards against the quickstart command silently giving a UI-only shell."""
    s = Settings(
        _env_file=None,
        AIFRED_DATA_DIR=str(tmp_path),
        AIFRED_BRAINMD_MCP_URL="",
        MCP_BRAIN_MD_API_KEY="",
        AIFRED_TELEGRAM_BOT_TOKEN="",
        AIFRED_GOOGLE_TOKEN_PATH="",
    )
    rt = build_runtime(s)
    assert rt.agent is not None  # what run_service() passes to create_app()
    # stub the agent's own run() so this test doesn't need a live LLM; the
    # wiring under test is create_app()/make_api(), not the agent loop itself
    monkeypatch.setattr(rt.agent, "run", lambda message, **kw: {"reply": f"echo: {message}"})
    app = create_app(agent=rt.agent, confirm=rt.confirm, whatsapp=rt.whatsapp,
                      store=rt.store, triage=rt.triage, contacts=rt.contacts)
    client = TestClient(app)
    resp = client.post("/api/chat", json={"message": "hello"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "echo: hello"
    rt.store.close()
