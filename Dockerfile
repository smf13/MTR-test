# ---- Stage 1: build the web UI -------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="MTR Tracker" \
      org.opencontainers.image.description="Continuous MTR monitoring with per-hop history" \
      org.opencontainers.image.source="https://github.com/smf13/MTR-test"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MTR_TRACKER_DATA_DIR=/data \
    MTR_TRACKER_STATIC_DIR=/app/static \
    MTR_TRACKER_PORT=8899 \
    MTR_TRACKER_HOST=0.0.0.0

# mtr-tiny provides the mtr binary; tini reaps zombies from short-lived mtr processes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends mtr-tiny tini ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=ui /ui/dist ./static

VOLUME ["/data"]
EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${MTR_TRACKER_PORT:-8899}/healthz" || exit 1

# mtr needs raw sockets: run as root with CAP_NET_RAW (see docker-compose.yml).
ENTRYPOINT ["/usr/bin/tini", "--"]
# app.main reads MTR_TRACKER_HOST / MTR_TRACKER_PORT, so overriding the port needs no CMD change.
CMD ["python", "-m", "app.main"]
