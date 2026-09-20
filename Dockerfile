# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    RINKCHECK_DB_PATH=/app/data/rinkcheck.sqlite3 \
    RINKCHECK_FRONTEND_DIR=/app/frontend/dist
WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && groupadd --gid 10001 rinkcheck \
    && useradd --uid 10001 --gid rinkcheck --no-create-home --shell /usr/sbin/nologin rinkcheck \
    && mkdir /app/data \
    && chown rinkcheck:rinkcheck /app/data
COPY --chown=rinkcheck:rinkcheck backend/rinkcheck/ /app/backend/rinkcheck/
COPY --chown=rinkcheck:rinkcheck fixtures/ /app/fixtures/
COPY --from=frontend --chown=rinkcheck:rinkcheck /build/frontend/dist/ /app/frontend/dist/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
# SQLite uses a local volume and one application worker. Do not scale replicas
# against the same SQLite file or replace this with a shared network mount.
CMD ["uvicorn", "rinkcheck.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
