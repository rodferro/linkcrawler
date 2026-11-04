"""Application settings, read from environment variables prefixed with ``CRAWLER_``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from linkcrawler import __version__

# Repository root when running from a source checkout (src/linkcrawler/config.py -> repo).
# Using an absolute anchor means the database location never depends on the cwd.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_USER_AGENT = f"LinkCrawler/{__version__} (+https://github.com/rodferro/tech-test)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRAWLER_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite+aiosqlite:///{_PROJECT_ROOT / 'data' / 'crawler.db'}"

    # Defaults for new crawls; each can be overridden per crawl from the form/API.
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = 10.0
    max_pages: int = 50
    max_depth: int = 2
    concurrency: int = 8
    per_host_concurrency: int = 2
    per_host_delay: float = 0.5
    max_body_bytes: int = 5 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
