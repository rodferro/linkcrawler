# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CRAWLER_DATABASE_URL=sqlite+aiosqlite:////data/crawler.db
RUN useradd --create-home --uid 1000 crawler \
    && mkdir /data && chown crawler:crawler /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
USER crawler
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "linkcrawler.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
