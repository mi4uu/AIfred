"""T1 config tests. Covers C7 ctx budget + V8 secret redaction."""

from aifred.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.model == "qwen3.6:27b"
    assert s.ollama_base_url == "http://localhost:11434/v1"
    assert s.cloud_fallback_enabled is False  # V1/C6: opt-in only


def test_ctx_budget_default_16k():
    # C7: target turn ctx <=16k, not hermes 64k
    s = Settings(_env_file=None)
    assert s.ctx_budget == 16384


def test_public_dict_redacts_secrets(monkeypatch):
    # V8: secret values never exposed
    monkeypatch.setenv("MCP_BRAIN_MD_API_KEY", "supersecret")
    monkeypatch.setenv("AIFRED_TELEGRAM_BOT_TOKEN", "")
    s = Settings(_env_file=None)
    pub = s.public_dict()
    assert pub["brainmd_api_key"] == "***set***"
    assert pub["telegram_bot_token"] == "***unset***"
    assert "supersecret" not in str(pub.values())
