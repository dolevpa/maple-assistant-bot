"""Runtime config. Reads the process env first, then /workspace/maplestory-bot/.env.

Values are re-read on every call, so adding XAI_API_KEY to .env (or the env of a new
process) takes effect without code changes; the .env file is picked up without a restart.
"""
import os
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parent / ".env"

DEFAULT_MODEL = "grok-4.7"          # current flagship per docs.x.ai/docs/models
DEFAULT_BASE_URL = "https://api.x.ai/v1"


def _get(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    if val:
        return val.strip()
    if ENV_FILE.exists():
        val = dotenv_values(ENV_FILE).get(name)
        if val:
            return val.strip()
    return default


def discord_token() -> str:
    return _get("DISCORD_BOT_TOKEN")


def xai_api_key() -> str:
    return _get("XAI_API_KEY")


def xai_model() -> str:
    return _get("XAI_MODEL", DEFAULT_MODEL)


def xai_base_url() -> str:
    return _get("XAI_BASE_URL", DEFAULT_BASE_URL)


def cooldown_seconds() -> float:
    try:
        return float(_get("ASK_COOLDOWN_SECONDS", "15"))
    except ValueError:
        return 15.0


def dev_guild_id() -> int | None:
    v = _get("DISCORD_GUILD_ID")
    return int(v) if v.isdigit() else None


def guides_refresh_hours() -> float:
    try:
        return float(_get("GUIDES_REFRESH_HOURS", "6"))
    except ValueError:
        return 6.0
