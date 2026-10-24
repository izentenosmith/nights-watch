FROM python:3.15-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

# the default catalog; compose mounts ./catalog over it
COPY catalog ./catalog
ENV NW_CATALOG_DIR=/app/catalog

# incidents live here; compose puts a named volume on it, which starts with this ownership
RUN mkdir -p /app/data && chown nobody /app/data
ENV NW_DB_PATH=/app/data/nightswatch.sqlite

USER nobody
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
# One worker: incidents are grouped under an in-process lock on a single SQLite file.
# More workers need a database that serializes writers (PLAN §8: Postgres).
CMD ["uvicorn", "nights_watch.bootstrap:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
