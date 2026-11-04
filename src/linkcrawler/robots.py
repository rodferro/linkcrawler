"""Async robots.txt fetching with a per-origin cache, parsed by ``urllib.robotparser``."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)


class RobotsCache:
    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def allowed(self, url: str) -> bool:
        parser = await self._parser_for(url)
        return parser.can_fetch(self._user_agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        parser = await self._parser_for(url)
        delay = parser.crawl_delay(self._user_agent)
        return float(delay) if delay is not None else None

    async def _parser_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._parsers:
            return self._parsers[origin]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._parsers:
                self._parsers[origin] = await self._fetch(origin)
        return self._parsers[origin]

    async def _fetch(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser(f"{origin}/robots.txt")
        try:
            response = await self._client.get(f"{origin}/robots.txt")
        except httpx.HTTPError as exc:
            # Unreachable robots.txt: treat as "no restrictions", like most crawlers.
            log.info("robots.txt for %s unavailable (%s); allowing all", origin, exc)
            parser.parse([])
            parser.modified()
            return parser

        # Mirrors RobotFileParser.read(): auth errors deny everything, other 4xx/5xx allow all.
        if response.status_code in (401, 403):
            parser.parse(["User-agent: *", "Disallow: /"])
        elif response.status_code >= 400:
            parser.parse([])
        else:
            parser.parse(response.text.splitlines())
        parser.modified()  # can_fetch() refuses everything until last_checked is set
        return parser
