from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI

from linkcrawler.config import Settings
from linkcrawler.main import create_app

SITE = "http://site.test"

HtmlFactory = Callable[..., str]


def html(*hrefs: str, title: str = "Page", extra: str = "") -> str:
    anchors = "".join(f'<a href="{h}">{h}</a>' for h in hrefs)
    return f"<html><head><title>{title}</title>{extra}</head><body>{anchors}</body></html>"


def page(*hrefs: str, title: str = "Page", extra: str = "") -> httpx.Response:
    return httpx.Response(200, html=html(*hrefs, title=title, extra=extra))


@pytest.fixture
def mock_http() -> Iterator[respx.MockRouter]:
    """Mock all outgoing HTTP. robots.txt is 404 (allow all) unless a test overrides it."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}/robots.txt", name="robots").respond(404)
        yield router


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        per_host_delay=0,
        request_timeout=2,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as c:
        yield c
