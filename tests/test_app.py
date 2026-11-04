from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import respx
from fastapi import FastAPI

from linkcrawler.config import Settings
from linkcrawler.db import Database
from linkcrawler.main import create_app
from linkcrawler.manager import CrawlManager
from linkcrawler.models import Crawl, CrawlStatus
from linkcrawler.schemas import CrawlCreate

from .conftest import SITE, page


def mock_site(router: respx.MockRouter) -> None:
    router.get(f"{SITE}/").mock(
        return_value=page("/a", "/b", "https://elsewhere.test/", title="Home")
    )
    router.get(f"{SITE}/a").mock(return_value=page("/", "/b", title="A"))
    router.get(f"{SITE}/b").respond(500)


async def run_crawl(client: httpx.AsyncClient, app: FastAPI, **body: object) -> dict[str, object]:
    response = await client.post("/api/crawls", json={"url": f"{SITE}/"} | body)
    assert response.status_code == 202, response.text
    crawl_id = response.json()["id"]
    manager: CrawlManager = app.state.manager
    await manager.wait(crawl_id)
    result: dict[str, object] = (await client.get(f"/api/crawls/{crawl_id}")).json()
    return result


async def test_api_crawl_end_to_end(
    client: httpx.AsyncClient, app: FastAPI, mock_http: respx.MockRouter
) -> None:
    mock_site(mock_http)

    crawl = await run_crawl(client, app, max_depth=3)

    assert crawl["status"] == "completed"
    assert (crawl["pages_crawled"], crawl["pages_failed"], crawl["links_found"]) == (3, 1, 5)
    started = datetime.fromisoformat(str(crawl["started_at"]))
    finished = datetime.fromisoformat(str(crawl["finished_at"]))
    assert started.tzinfo is not None  # serialized with an offset, so browsers don't guess
    assert timedelta(0) <= finished - started < timedelta(seconds=5)

    pages = (await client.get(f"/api/crawls/{crawl['id']}/pages")).json()
    assert {p["url"]: p["status_code"] for p in pages} == {
        f"{SITE}/": 200,
        f"{SITE}/a": 200,
        f"{SITE}/b": 500,
    }

    links = (await client.get(f"/api/crawls/{crawl['id']}/links", params={"q": "elsewhere"})).json()
    assert links == [
        {"source_url": f"{SITE}/", "target_url": "https://elsewhere.test/", "target_crawled": False}
    ]

    graph = (await client.get(f"/api/crawls/{crawl['id']}/graph")).json()
    assert {n["kind"] for n in graph["nodes"]} == {"start", "ok", "error"}
    assert len(graph["edges"]) == 4  # self-loops and uncrawled targets excluded
    full = (await client.get(f"/api/crawls/{crawl['id']}/graph?uncrawled=true")).json()
    assert len(full["nodes"]) == 4 and len(full["edges"]) == 5


async def test_graph_merges_http_https_variants(
    client: httpx.AsyncClient, app: FastAPI, mock_http: respx.MockRouter
) -> None:
    secure = "https://site.test"
    mock_http.get(f"{secure}/robots.txt").respond(404)
    mock_http.get(f"{secure}/").mock(return_value=page("/a"))
    mock_http.get(f"{secure}/a").mock(return_value=page(f"{SITE}/", "/"))

    crawl = await run_crawl(client, app, url=f"{secure}/")

    graph = (await client.get(f"/api/crawls/{crawl['id']}/graph")).json()
    assert sorted(n["id"] for n in graph["nodes"]) == [f"{secure}/", f"{secure}/a"]
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {
        (f"{secure}/", f"{secure}/a"),
        (f"{secure}/a", f"{secure}/"),
    }
    links = (await client.get(f"/api/crawls/{crawl['id']}/links")).json()
    assert all(link["target_crawled"] for link in links)


async def test_post_returns_before_crawl_finishes(
    client: httpx.AsyncClient, app: FastAPI, mock_http: respx.MockRouter
) -> None:
    """The old app blocked the POST until the whole crawl was done."""
    release = asyncio.Event()

    async def slow(_: httpx.Request) -> httpx.Response:
        await release.wait()
        return page()

    mock_http.get(f"{SITE}/").mock(side_effect=slow)

    response = await client.post("/api/crawls", json={"url": f"{SITE}/"})
    assert response.status_code == 202
    crawl_id = response.json()["id"]
    await asyncio.sleep(0.05)
    assert (await client.get(f"/api/crawls/{crawl_id}")).json()["status"] == "running"

    release.set()
    await app.state.manager.wait(crawl_id)
    assert (await client.get(f"/api/crawls/{crawl_id}")).json()["status"] == "completed"


async def test_cancel_crawl(
    client: httpx.AsyncClient, app: FastAPI, mock_http: respx.MockRouter
) -> None:
    async def hang(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return page()

    mock_http.get(f"{SITE}/").mock(side_effect=hang)
    crawl_id = (await client.post("/api/crawls", json={"url": f"{SITE}/"})).json()["id"]
    await asyncio.sleep(0.05)

    response = await client.post(f"/api/crawls/{crawl_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert (await client.post(f"/api/crawls/{crawl_id}/cancel")).status_code == 409


async def test_settings_supply_defaults(
    client: httpx.AsyncClient, app: FastAPI, mock_http: respx.MockRouter, settings: Settings
) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page())
    crawl = await run_crawl(client, app)
    assert crawl["max_pages"] == settings.max_pages
    assert crawl["max_depth"] == settings.max_depth
    assert crawl["user_agent"] == settings.user_agent
    assert "@" not in settings.user_agent  # no personal email in the default UA


async def test_api_validation(client: httpx.AsyncClient, mock_http: respx.MockRouter) -> None:
    for bad in ["mailto:x@y.z", "javascript:alert(1)", "ftp://site.test/", "http://"]:
        response = await client.post("/api/crawls", json={"url": bad})
        assert response.status_code == 422, bad
    assert (await client.post("/api/crawls", json={"url": SITE, "max_pages": 0})).status_code == 422
    assert (await client.get("/api/crawls/999")).json() == {"detail": "Crawl not found"}


async def test_form_starts_crawl_and_redirects(
    client: httpx.AsyncClient, app: FastAPI, mock_http: respx.MockRouter
) -> None:
    mock_site(mock_http)
    response = await client.post(
        "/crawls", data={"url": SITE, "max_depth": "1", "same_domain": "on", "timeout": ""}
    )
    assert response.status_code == 303
    location = response.headers["location"]
    crawl_id = int(location.rsplit("/", 1)[-1])
    await app.state.manager.wait(crawl_id)

    crawl = (await client.get(f"/api/crawls/{crawl_id}")).json()
    assert crawl["status"] == "completed"
    assert crawl["start_url"] == f"{SITE}/"
    assert crawl["max_depth"] == 1
    assert crawl["same_domain"] is True
    assert crawl["respect_robots"] is False  # unchecked box


async def test_form_validation_errors_rerender(
    client: httpx.AsyncClient, mock_http: respx.MockRouter
) -> None:
    response = await client.post("/crawls", data={"url": "javascript:alert(1)", "max_depth": "99"})
    assert response.status_code == 422
    assert "must be an http(s) URL" in response.text
    assert "less than or equal to 10" in response.text


async def test_html_pages_render(
    client: httpx.AsyncClient, app: FastAPI, mock_http: respx.MockRouter
) -> None:
    mock_site(mock_http)
    crawl = await run_crawl(client, app)
    crawl_id = crawl["id"]

    index = await client.get("/")
    assert index.status_code == 200 and f"{SITE}/" in index.text

    detail = await client.get(f"/crawls/{crawl_id}")
    assert detail.status_code == 200
    assert "HTTP 500" in detail.text and "Home" in detail.text

    links = await client.get(f"/crawls/{crawl_id}/links", params={"q": "elsewhere"})
    assert links.status_code == 200 and "1 link" in links.text

    graph = await client.get(f"/crawls/{crawl_id}/graph")
    assert graph.status_code == 200 and 'id="graph"' in graph.text

    missing = await client.get("/crawls/12345")
    assert missing.status_code == 404 and "Crawl not found" in missing.text


async def test_running_crawls_marked_interrupted_on_startup(settings: Settings) -> None:
    db = Database(settings.database_url)
    await db.create_all()
    async with db.session() as session, session.begin():
        session.add(
            Crawl(
                start_url=f"{SITE}/",
                status=CrawlStatus.RUNNING,
                max_depth=1,
                max_pages=1,
                same_domain=True,
                respect_robots=True,
                concurrency=1,
                per_host_concurrency=1,
                per_host_delay=0,
                timeout=1,
                user_agent="x",
            )
        )
    await db.dispose()

    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app") as client,
    ):
        crawls = (await client.get("/api/crawls")).json()
    assert crawls[0]["status"] == "interrupted"


def test_default_database_path_is_absolute(monkeypatch: object) -> None:
    """The old app used sqlite:///data.db, relative to whatever directory it was started from."""
    url = Settings().database_url
    path = Path(url.split(":///", 1)[1])
    assert path.is_absolute()


def test_bare_domains_get_https() -> None:
    assert CrawlCreate(url="example.com/a/").url == "https://example.com/a"
    assert CrawlCreate(url="localhost:8000").url == "https://localhost:8000/"
