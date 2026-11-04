"""Database reads and writes. All functions take an explicit session/database."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from linkcrawler.crawler import CrawlOptions, PageResult
from linkcrawler.db import Database
from linkcrawler.models import Crawl, CrawlStatus, Link, Page, utcnow
from linkcrawler.schemas import Graph, GraphEdge, GraphNode, LinkOut
from linkcrawler.urls import dedup_key

# --- crawls -------------------------------------------------------------------------


async def create_crawl(db: Database, options: CrawlOptions) -> Crawl:
    crawl = Crawl(
        start_url=options.start_url,
        status=CrawlStatus.QUEUED,
        max_depth=options.max_depth,
        max_pages=options.max_pages,
        same_domain=options.same_domain,
        respect_robots=options.respect_robots,
        concurrency=options.concurrency,
        per_host_concurrency=options.per_host_concurrency,
        per_host_delay=options.per_host_delay,
        timeout=options.timeout,
        user_agent=options.user_agent,
        pages_crawled=0,
        pages_failed=0,
        links_found=0,
    )
    async with db.write_lock, db.session() as session, session.begin():
        session.add(crawl)
    return crawl


async def set_status(
    db: Database,
    crawl_id: int,
    status: CrawlStatus,
    *,
    error: str | None = None,
) -> None:
    values: dict[str, object] = {"status": status}
    if status is CrawlStatus.RUNNING:
        values["started_at"] = utcnow()
    if status.finished:
        values["finished_at"] = utcnow()
    if error is not None:
        values["error"] = error
    async with db.write_lock, db.session() as session, session.begin():
        await session.execute(update(Crawl).where(Crawl.id == crawl_id).values(**values))


async def mark_unfinished_as_interrupted(db: Database) -> int:
    """Crawls run in-process, so any still 'running' at startup died with the old process."""
    async with db.write_lock, db.session() as session, session.begin():
        result = await session.execute(
            update(Crawl)
            .where(Crawl.status.in_([CrawlStatus.QUEUED, CrawlStatus.RUNNING]))
            .values(status=CrawlStatus.INTERRUPTED, finished_at=utcnow())
        )
    return int(getattr(result, "rowcount", 0) or 0)


async def get_crawl(session: AsyncSession, crawl_id: int) -> Crawl | None:
    return await session.get(Crawl, crawl_id)


async def list_crawls(session: AsyncSession, limit: int = 50) -> Sequence[Crawl]:
    result = await session.scalars(select(Crawl).order_by(Crawl.id.desc()).limit(limit))
    return result.all()


# --- pages & links ------------------------------------------------------------------


async def record_page(db: Database, crawl_id: int, result: PageResult) -> None:
    async with db.write_lock, db.session() as session, session.begin():
        page = Page(
            crawl_id=crawl_id,
            url=result.url,
            depth=result.depth,
            status_code=result.status_code,
            content_type=result.content_type,
            title=result.title,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
            outgoing_links=len(result.links),
            fetched_at=utcnow(),
        )
        session.add(page)
        await session.flush()
        if result.links:
            await session.execute(
                insert(Link),
                [
                    {"crawl_id": crawl_id, "source_page_id": page.id, "target_url": url}
                    for url in result.links
                ],
            )
        await session.execute(
            update(Crawl)
            .where(Crawl.id == crawl_id)
            .values(
                pages_crawled=Crawl.pages_crawled + 1,
                pages_failed=Crawl.pages_failed + (0 if result.ok else 1),
                links_found=Crawl.links_found + len(result.links),
            )
        )


async def list_pages(session: AsyncSession, crawl_id: int) -> Sequence[Page]:
    result = await session.scalars(
        select(Page).where(Page.crawl_id == crawl_id).order_by(Page.depth, Page.id)
    )
    return result.all()


async def list_links(
    session: AsyncSession,
    crawl_id: int,
    *,
    query: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[LinkOut], int]:
    """Return one page of edges plus the total count matching ``query``."""
    stmt = (
        select(Page.url, Link.target_url)
        .join(Page, Link.source_page_id == Page.id)
        .where(Link.crawl_id == crawl_id)
    )
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(Page.url.ilike(pattern), Link.target_url.ilike(pattern)))

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.execute(stmt.order_by(Link.id).offset(offset).limit(limit))
    crawled = await _crawled_keys(session, crawl_id)
    links = [
        LinkOut(source_url=s, target_url=t, target_crawled=dedup_key(t) in crawled) for s, t in rows
    ]
    return links, int(total or 0)


async def _crawled_keys(session: AsyncSession, crawl_id: int) -> dict[str, str]:
    """Map dedup key -> crawled page URL (so http/https variants resolve to one page)."""
    urls = await session.scalars(select(Page.url).where(Page.crawl_id == crawl_id))
    return {dedup_key(url): url for url in urls}


async def build_graph(
    session: AsyncSession,
    crawl: Crawl,
    *,
    include_uncrawled: bool = False,
    max_nodes: int = 1500,
) -> Graph:
    pages = await list_pages(session, crawl.id)
    page_by_id = {p.id: p for p in pages}
    crawled = {dedup_key(p.url): p.url for p in pages}
    page_urls = set(crawled.values())
    root_host = urlsplit(crawl.start_url).netloc

    nodes: dict[str, GraphNode] = {}
    for p in pages:
        kind = "start" if p.url == crawl.start_url else ("error" if p.error else "ok")
        nodes[p.url] = GraphNode(
            id=p.url,
            label=_short_label(p.url, root_host),
            kind=kind,
            depth=p.depth,
            status_code=p.status_code,
            title=p.title,
        )

    rows = await session.execute(
        select(Link.source_page_id, Link.target_url).where(Link.crawl_id == crawl.id)
    )
    edges: list[GraphEdge] = []
    truncated = False
    seen_edges: set[tuple[str, str]] = set()
    for source_id, raw_target in rows:
        source_url = page_by_id[source_id].url
        target_url = crawled.get(dedup_key(raw_target), raw_target)
        if target_url not in page_urls:
            if not include_uncrawled:
                continue
            if target_url not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    continue
                nodes[target_url] = GraphNode(
                    id=target_url, label=_short_label(target_url, root_host), kind="uncrawled"
                )
        if source_url != target_url and (source_url, target_url) not in seen_edges:
            seen_edges.add((source_url, target_url))
            edges.append(GraphEdge(source=source_url, target=target_url))
    return Graph(nodes=list(nodes.values()), edges=edges, truncated=truncated)


def _short_label(url: str, root_host: str) -> str:
    parts = urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    return path if parts.netloc == root_host else f"{parts.netloc}{path}"


def duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None:
        return None
    return ((end or utcnow()) - start).total_seconds()
