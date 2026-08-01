"""Environment-tiered runtime settings.

One Settings object, read once from the process environment. `.env` is
loaded only in development; staging/production get real process
environment variables from the deployment platform.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent.parent

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Environment = "development"

    # server
    host: str = "127.0.0.1"
    port: int = 8000

    # storage -- one database, not one-per-platform (see docs/adr/0004)
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "brand_intel"

    # paths
    log_path: Path = ROOT / "logs"
    # NOT the cookie pools (those live in Mongo's `sessions` collection) --
    # this is (a) where the one-off migration script reads the legacy
    # per-platform cookie JSON files from, and (b) where a non-cookie
    # session blob that genuinely has to be a file lives on, namely
    # Telethon's own MTProto `.session` sqlite file for Telegram.
    session_blob_path: Path = ROOT / "session"

    # alerts
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_pass: str = ""
    alert_emails: list[str] = Field(default_factory=list)
    alert_from: str = "alerts@brand-intelligence.local"

    # pacing -- the single most important knob for staying unremarkable
    request_timeout_sec: int = 45
    analysis_delay_sec: float = 6.0
    analysis_concurrency: int = 1
    discovery_concurrency: int = 2
    discovery_max_seconds: float = 300
    headless: bool = True

    # webhook callbacks (job completion push-back to the caller)
    webhook_timeout_sec: float = 10.0
    webhook_max_retries: int = 3

    @field_validator("alert_emails", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def write_env(key: str, value: str) -> None:
    """Persist one KEY=VALUE pair to .env and the live process environment.

    Used when a credential (a YouTube API key, Telegram's api_id/api_hash)
    arrives through the API instead of a hand-edited file, so it survives a
    restart without anyone touching .env directly. Development-tier
    convenience only -- staging/production rotate credentials through their
    own secrets store, not a file this process writes to itself.
    """
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, seen = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[key] = value


def _env_file_for(env: str) -> Optional[Path]:
    if env != "development":
        return None
    candidate = ROOT / ".env"
    return candidate if candidate.exists() else None


@lru_cache
def get_settings() -> Settings:
    env = os.environ.get("ENV", "development")
    env_file = _env_file_for(env)
    if env_file:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings()


settings = get_settings()
