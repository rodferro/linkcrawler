"""Pydantic models for the JSON API and form validation."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from linkcrawler.models import CrawlStatus
from linkcrawler.urls import normalize_url

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)")


class CrawlCreate(BaseModel):
    """Options for a new crawl. Omitted fields fall back to the server settings."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(max_length=2048)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    max_pages: int | None = Field(default=None, ge=1, le=2000)
    same_domain: bool = True
    respect_robots: bool = True
    concurrency: int | None = Field(default=None, ge=1, le=32)
    per_host_concurrency: int | None = Field(default=None, ge=1, le=16)
    per_host_delay: float | None = Field(default=None, ge=0, le=30)
    timeout: float | None = Field(default=None, gt=0, le=120)
    user_agent: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("url")
    @classmethod
    def _crawlable(cls, value: str) -> str:
        # Accept bare domains ("example.com", "localhost:8000") but not other schemes.
        if "://" not in value and not _SCHEME.match(value):
            value = f"https://{value}"
        normalized = normalize_url(value)
        if normalized is None:
            raise ValueError("must be an http(s) URL with a host")
        return normalized


class CrawlOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    start_url: str
    status: CrawlStatus
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    max_depth: int
    max_pages: int
    same_domain: bool
    respect_robots: bool
    concurrency: int
    per_host_concurrency: int
    per_host_delay: float
    timeout: float
    user_agent: str
    pages_crawled: int
    pages_failed: int
    links_found: int

    @property
    def finished(self) -> bool:
        return self.status.finished


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    depth: int
    status_code: int | None
    content_type: str | None
    title: str | None
    error: str | None
    elapsed_ms: int | None
    outgoing_links: int


class LinkOut(BaseModel):
    source_url: str
    target_url: str
    target_crawled: bool


class GraphNode(BaseModel):
    id: str
    label: str
    kind: str  # "start" | "ok" | "error" | "uncrawled"
    depth: int | None = None
    status_code: int | None = None
    title: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False
