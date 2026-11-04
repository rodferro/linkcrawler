from __future__ import annotations

import asyncio
import itertools
import time

import httpx
import pytest
import respx

from linkcrawler.crawler import Crawler, CrawlOptions, HostThrottle, PageResult, extract_links

from .conftest import SITE, page


async def crawl(**overrides: object) -> list[PageResult]:
    results: list[PageResult] = []

    async def on_page(result: PageResult) -> None:
        results.append(result)

    options: dict[str, object] = {"start_url": f"{SITE}/", "per_host_delay": 0} | overrides
    await Crawler(CrawlOptions(**options), on_page).run()  # type: ignore[arg-type]
    return results


def urls(results: list[PageResult]) -> set[str]:
    return {r.url for r in results}


# --- bug: no visited set -> pages re-fetched / infinite loop -----------------------


async def test_cycles_are_fetched_once(mock_http: respx.MockRouter) -> None:
    home = mock_http.get(f"{SITE}/").mock(return_value=page("/a", "/", "#top"))
    a = mock_http.get(f"{SITE}/a").mock(return_value=page("/b", "/"))
    b = mock_http.get(f"{SITE}/b").mock(return_value=page("/a", "/", "/b"))

    results = await crawl(max_depth=10, max_pages=100)

    assert urls(results) == {f"{SITE}/", f"{SITE}/a", f"{SITE}/b"}
    assert (home.call_count, a.call_count, b.call_count) == (1, 1, 1)


# --- bug: the 100-iteration cap counted links, not pages ----------------------------


async def test_max_pages_counts_pages_not_links(mock_http: respx.MockRouter) -> None:
    links = [f"/p{i}" for i in range(40)]
    mock_http.get(f"{SITE}/").mock(return_value=page(*links))
    child = mock_http.get(url__regex=rf"{SITE}/p\d+").mock(return_value=page(*links))

    results = await crawl(max_pages=5, max_depth=5)

    assert len(results) == 5
    assert child.call_count == 4
    # Every link on a fetched page is still reported, even when its target won't be crawled.
    assert all(len(r.links) == 40 for r in results)


async def test_max_depth(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page("/1"))
    mock_http.get(f"{SITE}/1").mock(return_value=page("/2"))
    mock_http.get(f"{SITE}/2").mock(return_value=page("/3"))
    deep = mock_http.get(f"{SITE}/3").mock(return_value=page())

    results = await crawl(max_depth=2)

    assert {r.url: r.depth for r in results} == {f"{SITE}/": 0, f"{SITE}/1": 1, f"{SITE}/2": 2}
    assert not deep.called


async def test_max_depth_zero_fetches_only_start(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page("/a"))
    results = await crawl(max_depth=0)
    assert urls(results) == {f"{SITE}/"}


# --- bug: relative links ignored, no normalization ---------------------------------


async def test_relative_links_followed_and_normalized(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(
        return_value=page("about", "/about/", "./about#team", "ABOUT", f"{SITE}:80/about")
    )
    about = mock_http.get(f"{SITE}/about").mock(return_value=page())
    upper = mock_http.get(f"{SITE}/ABOUT").mock(return_value=page())

    results = await crawl()

    assert about.call_count == 1
    assert upper.call_count == 1  # paths are case-sensitive
    assert results[0].links == [f"{SITE}/about", f"{SITE}/ABOUT"]


async def test_http_and_https_variants_crawled_once(mock_http: respx.MockRouter) -> None:
    secure = "https://site.test"
    mock_http.get(f"{secure}/robots.txt").respond(404)
    mock_http.get(f"{secure}/").mock(return_value=page(f"{SITE}/", f"{SITE}/about", "/about"))
    about = mock_http.get(f"{secure}/about").mock(return_value=page())
    insecure = mock_http.get(url__startswith=f"{SITE}/").mock(return_value=page())

    results = await crawl(start_url=f"{secure}/")

    assert urls(results) == {f"{secure}/", f"{secure}/about"}
    assert about.call_count == 1
    assert not insecure.called


async def test_base_href_is_respected(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page("guide", extra='<base href="/docs/">'))
    guide = mock_http.get(f"{SITE}/docs/guide").mock(return_value=page())
    await crawl()
    assert guide.called


async def test_redirect_target_not_refetched(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page("/old"))
    mock_http.get(f"{SITE}/old").respond(301, headers={"Location": f"{SITE}/docs/"})
    new = mock_http.get(f"{SITE}/docs/").mock(return_value=page("/docs", "rel"))
    rel = mock_http.get(f"{SITE}/docs/rel").mock(return_value=page())

    results = await crawl()

    assert new.call_count == 1  # reached via the redirect; its own link to itself is skipped
    assert rel.called  # relative links resolve against the final URL (with its slash)
    assert urls(results) == {f"{SITE}/", f"{SITE}/old", f"{SITE}/docs/rel"}


# --- same-domain option -------------------------------------------------------------


@pytest.mark.parametrize("same_domain", [True, False])
async def test_same_domain(mock_http: respx.MockRouter, same_domain: bool) -> None:
    mock_http.get(f"{SITE}/").mock(
        return_value=page("http://other.test/x", "http://www.site.test/w")
    )
    mock_http.get("http://other.test/robots.txt").respond(404)
    mock_http.get("http://www.site.test/robots.txt").respond(404)
    other = mock_http.get("http://other.test/x").mock(return_value=page())
    www = mock_http.get("http://www.site.test/w").mock(return_value=page())

    results = await crawl(same_domain=same_domain)

    assert other.called is not same_domain
    assert www.called  # www. is treated as the same site
    assert "http://other.test/x" in results[0].links  # the edge is recorded either way


# --- bug: no robots.txt --------------------------------------------------------------


async def test_robots_txt_disallow(mock_http: respx.MockRouter) -> None:
    mock_http.routes["robots"].respond(200, text="User-agent: *\nDisallow: /private\n")
    mock_http.get(f"{SITE}/").mock(return_value=page("/private/data", "/public"))
    private = mock_http.get(f"{SITE}/private/data").mock(return_value=page())
    mock_http.get(f"{SITE}/public").mock(return_value=page())

    results = await crawl()

    assert not private.called
    blocked = next(r for r in results if r.url.endswith("/private/data"))
    assert blocked.error == "Blocked by robots.txt"
    assert mock_http.routes["robots"].call_count == 1  # cached per origin


async def test_robots_txt_can_be_ignored(mock_http: respx.MockRouter) -> None:
    mock_http.routes["robots"].respond(200, text="User-agent: *\nDisallow: /\n")
    home = mock_http.get(f"{SITE}/").mock(return_value=page())
    await crawl(respect_robots=False)
    assert home.called
    assert not mock_http.routes["robots"].called


async def test_robots_txt_forbidden_disallows_all(mock_http: respx.MockRouter) -> None:
    mock_http.routes["robots"].respond(403)
    home = mock_http.get(f"{SITE}/").mock(return_value=page())
    results = await crawl()
    assert not home.called
    assert results[0].error == "Blocked by robots.txt"


async def test_robots_txt_unreachable_allows_all(mock_http: respx.MockRouter) -> None:
    mock_http.routes["robots"].mock(side_effect=httpx.ConnectError("refused"))
    home = mock_http.get(f"{SITE}/").mock(return_value=page())
    await crawl()
    assert home.called


# --- content type, errors, timeouts --------------------------------------------------


async def test_non_html_is_not_parsed(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page("/doc.pdf"))
    mock_http.get(f"{SITE}/doc.pdf").respond(
        200, content=b'<a href="/hidden">fake</a>', headers={"Content-Type": "application/pdf"}
    )
    hidden = mock_http.get(f"{SITE}/hidden").mock(return_value=page())

    results = await crawl()

    pdf = next(r for r in results if r.url.endswith(".pdf"))
    assert pdf.content_type == "application/pdf"
    assert pdf.links == [] and pdf.ok
    assert not hidden.called


async def test_http_errors_are_recorded(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page("/missing"))
    mock_http.get(f"{SITE}/missing").respond(404, html="<a href='/x'>x</a>")
    results = await crawl()
    missing = next(r for r in results if r.url.endswith("/missing"))
    assert (missing.status_code, missing.error, missing.links) == (404, "HTTP 404", [])


async def test_timeouts_do_not_stop_the_crawl(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page("/slow", "/fine"))
    mock_http.get(f"{SITE}/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    mock_http.get(f"{SITE}/fine").mock(return_value=page())

    results = await crawl(timeout=3)

    by_url = {r.url: r for r in results}
    assert by_url[f"{SITE}/slow"].error == "Timed out after 3s"
    assert by_url[f"{SITE}/fine"].ok


async def test_user_agent_is_configurable(mock_http: respx.MockRouter) -> None:
    home = mock_http.get(f"{SITE}/").mock(return_value=page())
    await crawl(user_agent="TestBot/1.0")
    assert home.calls.last.request.headers["User-Agent"] == "TestBot/1.0"


async def test_callback_errors_propagate(mock_http: respx.MockRouter) -> None:
    mock_http.get(f"{SITE}/").mock(return_value=page())

    async def boom(_: PageResult) -> None:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        await Crawler(CrawlOptions(start_url=f"{SITE}/"), boom).run()


def test_invalid_start_url() -> None:
    async def noop(_: PageResult) -> None: ...

    with pytest.raises(ValueError, match="crawlable"):
        Crawler(CrawlOptions(start_url="mailto:x@example.com"), noop)


# --- politeness: per-host concurrency + delay ----------------------------------------


async def test_per_host_concurrency_limit(mock_http: respx.MockRouter) -> None:
    in_flight = peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return page()

    mock_http.get(f"{SITE}/").mock(return_value=page(*[f"/p{i}" for i in range(12)]))
    mock_http.get(url__regex=rf"{SITE}/p\d+").mock(side_effect=slow)

    await crawl(concurrency=10, per_host_concurrency=3)

    assert peak == 3


async def test_per_host_delay_spaces_requests(mock_http: respx.MockRouter) -> None:
    starts: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        starts.append(time.monotonic())
        return page("/a", "/b", "/c")

    mock_http.get(url__regex=rf"{SITE}/(a|b|c)?$").mock(side_effect=record)

    await crawl(concurrency=4, per_host_concurrency=4, per_host_delay=0.05)

    assert len(starts) == 4
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert min(gaps) >= 0.045


async def test_robots_crawl_delay_is_honoured(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # urllib.robotparser only accepts whole seconds; record the delay instead of waiting it out.
    mock_http.routes["robots"].respond(200, text="User-agent: *\nCrawl-delay: 3\n")
    mock_http.get(f"{SITE}/").mock(return_value=page())
    delays: list[float] = []
    original = HostThrottle.slot

    def spy(self: HostThrottle, host: str, delay: float):  # type: ignore[no-untyped-def]
        delays.append(delay)
        return original(self, host, delay)

    monkeypatch.setattr(HostThrottle, "slot", spy)
    await crawl(per_host_delay=0.5)
    assert delays == [3.0]


# --- parsing -------------------------------------------------------------------------


def test_extract_links_title_and_dedup() -> None:
    body = b"""<html><head><title> Hello </title></head><body>
      <a href="/a">1</a><a href="/a#x">2</a><a>no href</a><a href="mailto:x@y.z">m</a>
      <a href="b/">3</a></body></html>"""
    title, links = extract_links(body, "https://ex.com/dir/")
    assert title == "Hello"
    assert links == ["https://ex.com/a", "https://ex.com/dir/b"]
