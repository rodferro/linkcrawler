"""Async engine/session setup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from linkcrawler.models import Base


class Database:
    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        self.is_sqlite = parsed.get_backend_name() == "sqlite"
        if self.is_sqlite and parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_async_engine(url)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)
        # SQLite allows one writer at a time; serialize our writes instead of
        # tripping over "database is locked" when several crawls run at once.
        self.write_lock = asyncio.Lock()

        if self.is_sqlite:
            event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
