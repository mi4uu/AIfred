"""Config loader. Reads env + .env (C5: secrets in env, never code).

All settings prefixed AIFRED_ except shared keys (OPENROUTER_API_KEY,
MCP_BRAIN_MD_API_KEY) reused from hermes ~/.hermes/.env convention.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # model (I.model, C1)
    ollama_base_url: str = Field(default="http://localhost:11434/v1", alias="AIFRED_OLLAMA_BASE_URL")
    model: str = Field(default="qwen3.6:27b", alias="AIFRED_MODEL")  # reliable agentic tool-use; 8B-vl fabricated saves (V30)
    ctx_budget: int = Field(default=16384, alias="AIFRED_CTX_BUDGET")  # C7: target <=16k
    ollama_keep_alive: str = Field(default="30m", alias="AIFRED_OLLAMA_KEEP_ALIVE")  # keep model resident
    chat_think: bool = Field(default=False, alias="AIFRED_CHAT_THINK")  # qwen thinking — off = fast chat
    # sampling (V28) — defaults tuned by aifred/eval for faithfulness on big context.
    # num_ctx is critical: ollama's runtime default is 4096 and silently truncates.
    num_ctx: int = Field(default=16384, alias="AIFRED_NUM_CTX")
    temperature: float = Field(default=0.0, alias="AIFRED_TEMPERATURE")
    top_p: float = Field(default=1.0, alias="AIFRED_TOP_P")
    top_k: int = Field(default=0, alias="AIFRED_TOP_K")
    repeat_penalty: float = Field(default=1.0, alias="AIFRED_REPEAT_PENALTY")
    min_p: float = Field(default=0.0, alias="AIFRED_MIN_P")
    cloud_fallback_enabled: bool = Field(default=False, alias="AIFRED_CLOUD_FALLBACK_ENABLED")  # V1/C6 opt-in
    # in-engine RAG (V31) — standalone embedder, resident alongside chat (MAX_LOADED_MODELS=2)
    rag_enabled: bool = Field(default=True, alias="AIFRED_RAG_ENABLED")
    embed_model: str = Field(default="qwen3-embedding:0.6b", alias="AIFRED_EMBED_MODEL")
    # vision on-demand (V36) — agent is 27b; a VL model is loaded only when an image needs reading
    vision_enabled: bool = Field(default=True, alias="AIFRED_VISION_ENABLED")
    vision_model: str = Field(default="qwen3-vl:8b-instruct-q8_0", alias="AIFRED_VISION_MODEL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")

    # brain.md MCP (I.brainmd)
    brainmd_mcp_url: str = Field(default="", alias="AIFRED_BRAINMD_MCP_URL")
    brainmd_api_key: str = Field(default="", alias="MCP_BRAIN_MD_API_KEY")
    brainmd_web_url: str = Field(default="", alias="AIFRED_BRAINMD_WEB_URL")  # human base for note context links (V27)

    # google (I.gmail, I.calendar)
    google_token_path: str = Field(default="", alias="AIFRED_GOOGLE_TOKEN_PATH")
    google_client_secret_path: str = Field(default="", alias="AIFRED_GOOGLE_CLIENT_SECRET_PATH")

    # telegram (I.telegram)
    telegram_bot_token: str = Field(default="", alias="AIFRED_TELEGRAM_BOT_TOKEN")
    telegram_allowed_users: str = Field(default="", alias="AIFRED_TELEGRAM_ALLOWED_USERS")
    telegram_home_channel: str = Field(default="", alias="AIFRED_TELEGRAM_HOME_CHANNEL")  # for push notifications

    # whatsapp (I.whatsapp) — neonize/whatsmeow
    whatsapp_enabled: bool = Field(default=False, alias="AIFRED_WHATSAPP_ENABLED")
    whatsapp_session_path: str = Field(default="", alias="AIFRED_WHATSAPP_SESSION")  # default data_dir/wa.sqlite
    whatsapp_important_senders: str = Field(default="", alias="AIFRED_WHATSAPP_IMPORTANT_SENDERS")
    owner_aliases: str = Field(default="Owner", alias="AIFRED_OWNER_ALIASES")  # who "you" is
    owner_whatsapp: str = Field(default="", alias="AIFRED_OWNER_WHATSAPP")  # owner's WA number (commands)
    owner_whatsapp_lid: str = Field(default="", alias="AIFRED_OWNER_WHATSAPP_LID")  # owner's @lid (own sent msgs)
    owner_email: str = Field(default="", alias="AIFRED_OWNER_EMAIL")  # self-mail = automated (V15)

    # autonomy — self-maintenance so AIfred runs without hand-holding (V24)
    contacts_writeback: bool = Field(default=True, alias="AIFRED_CONTACTS_WRITEBACK")  # fill confirmed WA numbers into ludzie/
    daily_note_enabled: bool = Field(default=True, alias="AIFRED_DAILY_NOTE")  # auto-compose yesterday's note
    daily_note_hour: int = Field(default=6, alias="AIFRED_DAILY_NOTE_HOUR")  # UTC hour after which to write it

    # store (I.store)
    data_dir: Path = Field(default=Path("./data"), alias="AIFRED_DATA_DIR")

    # web (I.web)
    web_host: str = Field(default="127.0.0.1", alias="AIFRED_WEB_HOST")
    web_port: int = Field(default=9120, alias="AIFRED_WEB_PORT")
    web_token: str = Field(default="", alias="AIFRED_WEB_TOKEN")  # if set, required on /api/*

    # secret keys — used to redact in public surfaces (V8)
    _SECRET_FIELDS = (
        "openrouter_api_key",
        "brainmd_api_key",
        "telegram_bot_token",
        "web_token",
    )

    def public_dict(self) -> dict[str, object]:
        """Config view safe to expose (web /config, logs). Secrets redacted (V8)."""
        out: dict[str, object] = {}
        for name in type(self).model_fields:
            val = getattr(self, name)
            if name in self._SECRET_FIELDS:
                out[name] = "***set***" if val else "***unset***"
            else:
                out[name] = str(val) if isinstance(val, Path) else val
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
