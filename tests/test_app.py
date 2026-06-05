"""Assembler tests — graceful degrade when services absent."""

from aifred.app import build_runtime
from aifred.config import Settings


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
