"""Application configuration, read from environment variables only.

Secrets (LLM_API_KEY / GROQ_API_KEY, SESSION_SECRET, OAuth secrets) live in the
process environment or server/.env and are never committed or sent to clients.
"""
from __future__ import annotations

import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SERVER_DIR.parent


def _load_dotenv() -> None:
    """Minimal .env loader (server/.env) so no extra dependency is needed."""
    env_file = SERVER_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    APP_ENV: str = os.environ.get("APP_ENV", "development")
    APP_BASE_URL: str = os.environ.get("APP_BASE_URL", "http://localhost:8000")
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{SERVER_DIR / 'data' / 'intenda.db'}"
    )
    SESSION_SECRET: str = os.environ.get("SESSION_SECRET", "dev-only-secret-change-me")
    TOKEN_ENCRYPTION_KEY: str = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

    # Agent model provider (OpenAI-compatible). LLM_* wins, GROQ_* is fallback.
    LLM_BASE_URL: str = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("GROQ_BASE_URL")
        or "https://api.groq.com/openai/v1"
    )
    LLM_API_KEY: str = os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY") or ""
    LLM_MODEL: str = (
        os.environ.get("LLM_MODEL") or os.environ.get("GROQ_MODEL") or "openai/gpt-oss-20b"
    )
    LLM_PROVIDER_NAME: str = "groq" if "groq" in LLM_BASE_URL else "openai-compatible"

    GOOGLE_CLIENT_ID: str = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    MICROSOFT_CLIENT_ID: str = os.environ.get("MICROSOFT_CLIENT_ID", "")
    MICROSOFT_CLIENT_SECRET: str = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
    OAUTH_REDIRECT_BASE_URL: str = os.environ.get("OAUTH_REDIRECT_BASE_URL", APP_BASE_URL)

    DEMO_MODE_ENABLED: bool = _bool("DEMO_MODE_ENABLED", True)
    DEMO_SYNC_DELAY_MS: int = int(os.environ.get("DEMO_SYNC_DELAY_MS", "700"))
    AGENT_REQUESTS_PER_MINUTE: int = int(os.environ.get("AGENT_REQUESTS_PER_MINUTE", "10"))
    AGENT_REQUESTS_PER_DAY: int = int(os.environ.get("AGENT_REQUESTS_PER_DAY", "100"))
    # Two-phase confirmation protocol (app/confirmations.py): a prepared
    # agent write action must be confirmed with its one-time token before
    # this many minutes pass, or the token expires and a fresh action has
    # to be prepared.
    CONFIRMATION_TTL_MINUTES: int = int(os.environ.get("CONFIRMATION_TTL_MINUTES", "15"))

    SESSION_ABSOLUTE_DAYS: int = 7
    SESSION_IDLE_HOURS: int = 24
    COOKIE_SECURE: bool = APP_ENV in {"production", "demo"} or APP_BASE_URL.startswith("https://")
    FRONTEND_DIST: str = os.environ.get("FRONTEND_DIST", str(REPO_DIR / "dist"))
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


settings = Settings()
