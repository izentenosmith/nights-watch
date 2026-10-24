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

USER nobody
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
CMD ["uvicorn", "nights_watch.bootstrap:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
