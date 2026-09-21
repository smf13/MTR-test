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

# HOME belongs to the unprivileged user: the entrypoint's setpriv keeps root's environment, and asyncpg searches HOME
# for client certificates before it connects, which must never land in the unreadable /root.
ENV HOME=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MTR_TRACKER_DATA_DIR=/data \
    MTR_TRACKER_STATIC_DIR=/app/static \
    MTR_TRACKER_PORT=8899 \
    MTR_TRACKER_HOST=0.0.0.0

# mtr-tiny and iputils-ping provide the probe binaries; tini reaps zombies from short-lived processes;
# libcap2-bin supplies setcap/getcap. The cap_net_raw file capability lets mtr-packet and ping open raw
# sockets without the process being root (Debian's packages set it too; repeating it makes it explicit).
RUN apt-get update \
 && apt-get install -y --no-install-recommends mtr-tiny iputils-ping tini ca-certificates curl libcap2-bin \
 && rm -rf /var/lib/apt/lists/* \
 && (setcap cap_net_raw+ep /usr/bin/mtr-packet && setcap cap_net_raw+ep "$(command -v ping)" \
     || echo "warning: setcap failed; the entrypoint will keep running as root" >&2) \
 && groupadd --gid 1000 mtr \
 && useradd --uid 1000 --gid mtr --home-dir /app --no-create-home --shell /usr/sbin/nologin mtr

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=ui /ui/dist ./static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh && mkdir -p /data && chown mtr:mtr /app /data

VOLUME ["/data"]
EXPOSE 8899

# The start period covers waiting for PostgreSQL and, on an upgraded installation, the one-time SQLite import.
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${MTR_TRACKER_PORT:-8899}/healthz" || exit 1

# The entrypoint fixes the ownership of /data as root and then drops to the "mtr" user (see docker-entrypoint.sh).
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
# app.main reads MTR_TRACKER_HOST / MTR_TRACKER_PORT, so overriding the port needs no CMD change.
CMD ["python", "-m", "app.main"]
