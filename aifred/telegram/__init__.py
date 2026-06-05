"""Telegram interface — allowed-users gate + agent routing."""

from aifred.telegram.bot import TelegramBot, parse_allowed_users

__all__ = ["TelegramBot", "parse_allowed_users"]
