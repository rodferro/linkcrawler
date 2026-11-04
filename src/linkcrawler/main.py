"""Application factory. Run with ``uvicorn linkcrawler.main:app``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from linkcrawler import __version__, api, web
from linkcrawler import repository as repo
from linkcrawler.config import Settings, get_settings
from linkcrawler.db import Database
from linkcrawler.manager import CrawlManager

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        await db.create_all()
        if interrupted := await repo.mark_unfinished_as_interrupted(db):
            log.warning("Marked %d unfinished crawl(s) as interrupted", interrupted)
        app.state.db = db
        app.state.manager = CrawlManager(db, settings)
        try:
            yield
        finally:
            await app.state.manager.shutdown()
            await db.dispose()

    app = FastAPI(title="LinkCrawler", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(api.router)
    app.include_router(web.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return web.templates.TemplateResponse(
            request, "error.html", {"exc": exc}, status_code=exc.status_code
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
