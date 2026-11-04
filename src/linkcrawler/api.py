"""JSON API under /api."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from linkcrawler import repository as repo
from linkcrawler.deps import CrawlDep, ManagerDep, SessionDep
from linkcrawler.schemas import CrawlCreate, CrawlOut, Graph, LinkOut, PageOut

router = APIRouter(prefix="/api", tags=["api"])


@router.post("/crawls", status_code=status.HTTP_202_ACCEPTED)
async def create_crawl(body: CrawlCreate, manager: ManagerDep, session: SessionDep) -> CrawlOut:
    """Start a crawl in the background. Poll ``GET /api/crawls/{id}`` for progress."""
    crawl_id = await manager.start(body)
    crawl = await repo.get_crawl(session, crawl_id)
    return CrawlOut.model_validate(crawl)


@router.get("/crawls")
async def list_crawls(session: SessionDep) -> list[CrawlOut]:
    return [CrawlOut.model_validate(c) for c in await repo.list_crawls(session)]


@router.get("/crawls/{crawl_id}")
async def get_crawl(crawl: CrawlDep) -> CrawlOut:
    return CrawlOut.model_validate(crawl)


@router.post("/crawls/{crawl_id}/cancel")
async def cancel_crawl(crawl: CrawlDep, manager: ManagerDep, session: SessionDep) -> CrawlOut:
    if not await manager.cancel(crawl.id):
        raise HTTPException(status_code=409, detail="Crawl is not running")
    await session.refresh(crawl)
    return CrawlOut.model_validate(crawl)


@router.get("/crawls/{crawl_id}/pages")
async def get_pages(crawl: CrawlDep, session: SessionDep) -> list[PageOut]:
    return [PageOut.model_validate(p) for p in await repo.list_pages(session, crawl.id)]


@router.get("/crawls/{crawl_id}/links")
async def get_links(
    crawl: CrawlDep,
    session: SessionDep,
    q: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[LinkOut]:
    links, _total = await repo.list_links(session, crawl.id, query=q, offset=offset, limit=limit)
    return links


@router.get("/crawls/{crawl_id}/graph")
async def get_graph(crawl: CrawlDep, session: SessionDep, uncrawled: bool = False) -> Graph:
    return await repo.build_graph(session, crawl, include_uncrawled=uncrawled)
