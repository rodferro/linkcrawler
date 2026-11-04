"""Runs crawls as in-process asyncio background tasks and tracks them."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from linkcrawler import repository as repo
from linkcrawler.config import Settings
from linkcrawler.crawler import Crawler, CrawlOptions
from linkcrawler.db import Database
from linkcrawler.models import CrawlStatus
from linkcrawler.schemas import CrawlCreate

log = logging.getLogger(__name__)


class CrawlManager:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def options_from_request(self, req: CrawlCreate) -> CrawlOptions:
        s = self.settings
        return CrawlOptions(
            start_url=req.url,
            max_depth=s.max_depth if req.max_depth is None else req.max_depth,
            max_pages=req.max_pages or s.max_pages,
            same_domain=req.same_domain,
            respect_robots=req.respect_robots,
            concurrency=req.concurrency or s.concurrency,
            per_host_concurrency=req.per_host_concurrency or s.per_host_concurrency,
            per_host_delay=s.per_host_delay if req.per_host_delay is None else req.per_host_delay,
            timeout=req.timeout or s.request_timeout,
            user_agent=req.user_agent or s.user_agent,
            max_body_bytes=s.max_body_bytes,
        )

    async def start(self, req: CrawlCreate) -> int:
        """Persist a new crawl and launch it in the background; returns its id immediately."""
        options = self.options_from_request(req)
        crawl = await repo.create_crawl(self.db, options)
        task = asyncio.create_task(self._run(crawl.id, options), name=f"crawl-{crawl.id}")
        self._tasks[crawl.id] = task
        task.add_done_callback(partial(self._forget, crawl.id))
        return crawl.id

    def _forget(self, crawl_id: int, _task: asyncio.Task[None]) -> None:
        self._tasks.pop(crawl_id, None)

    def is_running(self, crawl_id: int) -> bool:
        return crawl_id in self._tasks

    async def cancel(self, crawl_id: int) -> bool:
        task = self._tasks.get(crawl_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def wait(self, crawl_id: int) -> None:
        task = self._tasks.get(crawl_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, crawl_id: int, options: CrawlOptions) -> None:
        await repo.set_status(self.db, crawl_id, CrawlStatus.RUNNING)
        try:
            crawler = Crawler(options, on_page=partial(repo.record_page, self.db, crawl_id))
            stats = await crawler.run()
        except asyncio.CancelledError:
            await asyncio.shield(repo.set_status(self.db, crawl_id, CrawlStatus.CANCELLED))
            raise
        except Exception as exc:
            log.exception("Crawl %s failed", crawl_id)
            await repo.set_status(self.db, crawl_id, CrawlStatus.FAILED, error=str(exc))
        else:
            log.info("Crawl %s done: %s", crawl_id, stats)
            await repo.set_status(self.db, crawl_id, CrawlStatus.COMPLETED)
