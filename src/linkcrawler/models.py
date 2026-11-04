"""SQLAlchemy ORM models: crawls -> pages -> links (edges)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC and always returns timezone-aware datetimes.

    SQLite has no timezone support and hands back naive values, which then serialize
    without an offset and get misread as local time by browsers.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class CrawlStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the server stopped while the crawl was running

    @property
    def finished(self) -> bool:
        return self not in (CrawlStatus.QUEUED, CrawlStatus.RUNNING)


class Crawl(Base):
    __tablename__ = "crawls"

    id: Mapped[int] = mapped_column(primary_key=True)
    start_url: Mapped[str] = mapped_column(Text)
    status: Mapped[CrawlStatus] = mapped_column(
        SAEnum(CrawlStatus, native_enum=False, length=16), default=CrawlStatus.QUEUED
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Options the crawl ran with.
    max_depth: Mapped[int] = mapped_column(Integer)
    max_pages: Mapped[int] = mapped_column(Integer)
    same_domain: Mapped[bool] = mapped_column(Boolean)
    respect_robots: Mapped[bool] = mapped_column(Boolean)
    concurrency: Mapped[int] = mapped_column(Integer)
    per_host_concurrency: Mapped[int] = mapped_column(Integer)
    per_host_delay: Mapped[float] = mapped_column(Float)
    timeout: Mapped[float] = mapped_column(Float)
    user_agent: Mapped[str] = mapped_column(String(255))

    # Progress counters, updated as pages are stored.
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, default=0)
    links_found: Mapped[int] = mapped_column(Integer, default=0)

    pages: Mapped[list[Page]] = relationship(
        back_populates="crawl", cascade="all, delete-orphan", passive_deletes=True
    )


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("crawl_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_id: Mapped[int] = mapped_column(ForeignKey("crawls.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    outgoing_links: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    crawl: Mapped[Crawl] = relationship(back_populates="pages")


class Link(Base):
    """A directed edge: ``source_page`` contains an ``<a href>`` pointing at ``target_url``.

    The target is stored as a URL, not a page id, because many targets are never crawled
    (off-site, beyond max depth/pages, or blocked by robots.txt).
    """

    __tablename__ = "links"
    __table_args__ = (
        UniqueConstraint("source_page_id", "target_url"),
        Index("ix_links_crawl_target", "crawl_id", "target_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_id: Mapped[int] = mapped_column(ForeignKey("crawls.id", ondelete="CASCADE"))
    source_page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    target_url: Mapped[str] = mapped_column(Text)
