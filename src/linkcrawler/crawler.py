"""The crawl engine: a breadth-first, concurrent crawler built on asyncio + httpx.

The crawler knows nothing about the database. It reports each processed page through an
async ``on_page`` callback, which keeps it easy to test and reuse.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from linkcrawler.config import DEFAULT_USER_AGENT
from linkcrawler.robots import RobotsCache
from linkcrawler.urls import dedup_key, host_of, normalize_url, same_site

log = logging.getLogger(__name__)

HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})


@dataclass(frozen=True, slots=True)
class CrawlOptions:
    start_url: str
    max_depth: int = 2
    max_pages: int = 50
    same_domain: bool = True
    respect_robots: bool = True
    concurrency: int = 8
    per_host_concurrency: int = 2
    per_host_delay: float = 0.5
    timeout: float = 10.0
    user_agent: str = DEFAULT_USER_AGENT
    max_body_bytes: int = 5 * 1024 * 1024


@dataclass(slots=True)
class PageResult:
    url: str
    depth: int
    status_code: int | None = None
    content_type: str | None = None
    title: str | None = None
    links: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class CrawlStats:
    pages: int = 0
    failed: int = 0
    links: int = 0


PageCallback = Callable[[PageResult], Awaitable[None]]


class HostThrottle:
    """Caps concurrent requests per host and spaces out request start times."""

    def __init__(self, per_host_concurrency: int) -> None:
        self._limit = per_host_concurrency
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_start: dict[str, float] = {}

    @contextlib.asynccontextmanager
    async def slot(self, host: str, delay: float) -> AsyncIterator[None]:
        semaphore = self._semaphores.setdefault(host, asyncio.Semaphore(self._limit))
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with semaphore:
            async with lock:
                now = time.monotonic()
                start_at = max(now, self._next_start.get(host, now))
                self._next_start[host] = start_at + delay
            if start_at > now:
                await asyncio.sleep(start_at - now)
            yield


def extract_links(
    html: bytes, base_url: str, encoding: str | None = None
) -> tuple[str | None, list[str]]:
    """Return the page title and the de-duplicated, normalized outgoing links."""
    soup = BeautifulSoup(html, "html.parser", from_encoding=encoding)
    base_tag = soup.find("base", href=True)
    if isinstance(base_tag, Tag):
        # Resolve but don't normalize: a trailing slash on a base URL changes resolution.
        base_url = urljoin(base_url, str(base_tag["href"]).strip())

    title = soup.title.get_text(strip=True) if soup.title else None
    links: dict[str, None] = {}  # ordered set
    for anchor in soup.find_all("a", href=True):
        url = normalize_url(str(anchor["href"]), base_url)
        if url is not None:
            links[url] = None
    return title or None, list(links)


class Crawler:
    def __init__(
        self,
        options: CrawlOptions,
        on_page: PageCallback,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        start = normalize_url(options.start_url)
        if start is None:
            raise ValueError(f"Not a crawlable http(s) URL: {options.start_url!r}")
        self.options = options
        self.start_url = start
        self.root_host = host_of(start)
        self._prefer_https = start.startswith("https:")
        self._on_page = on_page
        self._client = client
        self._seen: set[str] = set()
        self._scheduled = 0
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._throttle = HostThrottle(options.per_host_concurrency)
        self._robots: RobotsCache | None = None
        self.stats = CrawlStats()

    async def run(self) -> CrawlStats:
        async with contextlib.AsyncExitStack() as stack:
            client = self._client
            if client is None:
                client = await stack.enter_async_context(self._make_client())
            self._robots = RobotsCache(client, self.options.user_agent)
            self._schedule(self.start_url, 0)
            await self._run_workers(client)
        return self.stats

    def _make_client(self) -> httpx.AsyncClient:
        limit = self.options.concurrency
        return httpx.AsyncClient(
            headers={"User-Agent": self.options.user_agent},
            timeout=self.options.timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=limit, max_keepalive_connections=limit),
        )

    async def _run_workers(self, client: httpx.AsyncClient) -> None:
        workers = [
            asyncio.create_task(self._worker(client), name=f"crawl-worker-{i}")
            for i in range(self.options.concurrency)
        ]
        drained = asyncio.create_task(self._queue.join())
        try:
            done, _ = await asyncio.wait([drained, *workers], return_when=asyncio.FIRST_COMPLETED)
            # Workers loop forever, so one finishing means it raised: surface the error.
            for task in done:
                if task is not drained:
                    task.result()
        finally:
            for task in [drained, *workers]:
                task.cancel()
            await asyncio.gather(drained, *workers, return_exceptions=True)

    def _schedule(self, url: str, depth: int) -> None:
        if self._prefer_https and url.startswith("http:") and same_site(url, self.root_host):
            url = "https:" + url[len("http:") :]
        key = dedup_key(url)
        if key in self._seen:
            return
        if self.options.same_domain and not same_site(url, self.root_host):
            return
        if self._scheduled >= self.options.max_pages:
            return
        self._seen.add(key)
        self._scheduled += 1
        self._queue.put_nowait((url, depth))

    async def _worker(self, client: httpx.AsyncClient) -> None:
        while True:
            url, depth = await self._queue.get()
            try:
                result = await self._process(client, url, depth)
                self.stats.pages += 1
                self.stats.links += len(result.links)
                if not result.ok:
                    self.stats.failed += 1
                await self._on_page(result)
                if depth < self.options.max_depth:
                    for link in result.links:
                        self._schedule(link, depth + 1)
            finally:
                self._queue.task_done()

    async def _process(self, client: httpx.AsyncClient, url: str, depth: int) -> PageResult:
        result = PageResult(url=url, depth=depth)
        assert self._robots is not None
        delay = self.options.per_host_delay
        if self.options.respect_robots:
            if not await self._robots.allowed(url):
                result.error = "Blocked by robots.txt"
                return result
            delay = max(delay, await self._robots.crawl_delay(url) or 0.0)

        try:
            async with self._throttle.slot(host_of(url), delay):
                started = time.monotonic()  # time the request, not the politeness wait
                try:
                    body, final_url, encoding = await self._fetch(client, url, result)
                finally:
                    result.elapsed_ms = round((time.monotonic() - started) * 1000)
        except httpx.TimeoutException:
            result.error = f"Timed out after {self.options.timeout:g}s"
            return result
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            return result

        if body is None:
            return result

        # A redirect target is the same page; don't fetch it again later.
        normalized_final = normalize_url(final_url)
        if normalized_final and normalized_final != url:
            self._seen.add(dedup_key(normalized_final))

        result.title, result.links = await asyncio.to_thread(
            extract_links, body, final_url, encoding
        )
        return result

    async def _fetch(
        self, client: httpx.AsyncClient, url: str, result: PageResult
    ) -> tuple[bytes | None, str, str | None]:
        """GET ``url``; return (html body or None, final url, charset)."""
        async with client.stream("GET", url) as response:
            result.status_code = response.status_code
            content_type = response.headers.get("content-type", "")
            result.content_type = content_type.split(";", 1)[0].strip().lower() or None
            final_url = str(response.url)

            if response.status_code >= 400:
                result.error = f"HTTP {response.status_code}"
                return None, final_url, None
            if result.content_type not in HTML_CONTENT_TYPES:
                # Check headers before downloading: skip images, PDFs, archives, ...
                return None, final_url, None

            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= self.options.max_body_bytes:
                    log.info("Truncating %s at %d bytes", url, size)
                    break
            return b"".join(chunks), final_url, response.charset_encoding
