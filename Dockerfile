# OmniQuery in one image: the React build served by the FastAPI backend on one port.

# 1. Frontend
FROM oven/bun:1 AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# 2. Backend
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
# Use the image's Python rather than the exact patch pinned in .python-version.
ENV UV_PYTHON=/usr/local/bin/python \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1 \
    OMNIQUERY_HOST=0.0.0.0

# Owned by a normal user from the start: a chown afterwards would copy every file into a new layer.
RUN useradd --create-home app && mkdir -p /app/backend && chown -R app /app
USER app
WORKDIR /app/backend
COPY --chown=app backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --chown=app backend/ ./
RUN uv sync --frozen --no-dev
COPY --chown=app --from=web /app/frontend/dist /app/frontend/dist

# Hosts like Render set $PORT; `serve` reads it and defaults to 7666.
EXPOSE 7666
CMD [".venv/bin/omniquery", "serve", "--no-browser"]
