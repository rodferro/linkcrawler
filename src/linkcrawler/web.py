"""Server-rendered HTML pages."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from linkcrawler import repository as repo
from linkcrawler.deps import CrawlDep, ManagerDep, SessionDep
from linkcrawler.schemas import CrawlCreate

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["duration"] = repo.duration_seconds

router = APIRouter(include_in_schema=False)

LINKS_PER_PAGE = 100
_CHECKBOXES = ("same_domain", "respect_robots")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionDep, manager: ManagerDep) -> Response:
    return await _render_index(request, session, manager)


@router.post("/crawls", response_class=HTMLResponse)
async def start_crawl(request: Request, session: SessionDep, manager: ManagerDep) -> Response:
    form = await request.form()
    data: dict[str, Any] = {k: v for k, v in form.items() if k not in _CHECKBOXES and v != ""}
    for name in _CHECKBOXES:  # unchecked boxes are simply absent from the form
        data[name] = name in form
    try:
        body = CrawlCreate.model_validate(data)
    except ValidationError as exc:
        errors = {str(e["loc"][0]): e["msg"] for e in exc.errors() if e["loc"]}
        return await _render_index(
            request,
            session,
            manager,
            form=data,
            errors=errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    crawl_id = await manager.start(body)
    return RedirectResponse(
        request.url_for("crawl_detail", crawl_id=crawl_id), status.HTTP_303_SEE_OTHER
    )


@router.get("/crawls/{crawl_id}", response_class=HTMLResponse, name="crawl_detail")
async def crawl_detail(request: Request, crawl: CrawlDep, session: SessionDep) -> Response:
    pages = await repo.list_pages(session, crawl.id)
    return templates.TemplateResponse(
        request, "crawl.html", {"crawl": crawl, "pages": pages, "tab": "pages"}
    )


@router.get("/crawls/{crawl_id}/links", response_class=HTMLResponse, name="crawl_links")
async def crawl_links(
    request: Request,
    crawl: CrawlDep,
    session: SessionDep,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    links, total = await repo.list_links(
        session, crawl.id, query=q, offset=(page - 1) * LINKS_PER_PAGE, limit=LINKS_PER_PAGE
    )
    return templates.TemplateResponse(
        request,
        "links.html",
        {
            "crawl": crawl,
            "links": links,
            "total": total,
            "q": q or "",
            "page": page,
            "pages_total": max(1, -(-total // LINKS_PER_PAGE)),
            "tab": "links",
        },
    )


@router.get("/crawls/{crawl_id}/graph", response_class=HTMLResponse, name="crawl_graph")
async def crawl_graph(request: Request, crawl: CrawlDep) -> Response:
    return templates.TemplateResponse(request, "graph.html", {"crawl": crawl, "tab": "graph"})


async def _render_index(
    request: Request,
    session: SessionDep,
    manager: ManagerDep,
    *,
    form: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> Response:
    crawls = await repo.list_crawls(session)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "crawls": crawls,
            "defaults": manager.settings,
            "form": form or {"same_domain": True, "respect_robots": True},
            "errors": errors or {},
        },
        status_code=status_code,
    )
