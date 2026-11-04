"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from linkcrawler import repository as repo
from linkcrawler.db import Database
from linkcrawler.manager import CrawlManager
from linkcrawler.models import Crawl


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_manager(request: Request) -> CrawlManager:
    manager: CrawlManager = request.app.state.manager
    return manager


async def get_session(db: Annotated[Database, Depends(get_db)]) -> AsyncIterator[AsyncSession]:
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[CrawlManager, Depends(get_manager)]


async def get_crawl_or_404(crawl_id: int, session: SessionDep) -> Crawl:
    crawl = await repo.get_crawl(session, crawl_id)
    if crawl is None:
        raise HTTPException(status_code=404, detail="Crawl not found")
    return crawl


CrawlDep = Annotated[Crawl, Depends(get_crawl_or_404)]
