# MTR Tracker

Continuous MTR monitoring for IT professionals: a FastAPI + SQLite backend runs `mtr --json` against user-defined targets on a schedule, stores every hop of every run, and a React UI visualises latency, loss, jitter, per-hop history and route changes.

## Layout

```
backend/app/        Python package (run with `python -m uvicorn app.main:app` from backend/)
  main.py           FastAPI app factory, lifespan (DB + scheduler), SPA static serving
  api.py            All HTTP routes under /api
  scheduler.py      24/7 loop: due targets -> run -> store -> classify -> events/webhooks; retention purge
  mtr.py            mtr command builder, JSON parser, route signature/compare, simulator
  notify.py         Notification channels (webhook, Pushover); dispatch_event fans one payload out to all enabled channels
  resolver.py       forward + reverse DNS with TTL cache
  db.py             SQLite schema (targets, runs, hops, events, settings) and helpers
  models.py         Pydantic request schemas
  config.py         Environment variables (MTR_TRACKER_*), evaluated at import time
backend/tests/      pytest (asyncio mode auto); API tests run the app in simulation mode
frontend/src/       React 19 + TypeScript + Vite 8 + Tailwind 4 + Recharts 3
  api.ts            Typed API client and shared interfaces
  utils.ts          Formatting, colour scales, status helpers
  hooks.ts          usePoll (visibility-aware polling), useTheme, useLocalStorage
  pages/            Dashboard, TargetDetail, RunView, Events, Settings, QuickTrace
  components/       Charts, HopHeatmap, HopTable, PathSummary, RunsTable, EventsList, TargetForm, Layout
Dockerfile          Multi-stage: node build of frontend -> python:3.12-slim with mtr-tiny + tini
docker-compose.yml  Grants NET_RAW, persistent /data volume, healthcheck on /healthz
```

## Commands

```bash
# Backend
cd backend && pip install -r requirements-dev.txt
MTR_TRACKER_SIMULATE=1 MTR_TRACKER_DATA_DIR=./data python -m uvicorn app.main:app --reload --port 8899
python -m pytest -q

# Frontend
cd frontend && npm install
npm run dev          # Vite on :5173, proxies /api to :8899
npm run build        # tsc --noEmit && vite build -> frontend/dist (served by the backend if present)

# Docker
docker compose up -d --build
```

## Conventions and gotchas

- **Naming**: the product is "MTR Tracker". Identifiers use `mtr-tracker` (logger names, image, volume) and env vars use the `MTR_TRACKER_` prefix. Do not reintroduce other names.
- **Config is import-time**: `config.py` builds a frozen `Config` when imported. Tests that change env vars reload `config`, `mtr`, `scheduler`, `api` and `main` (see `tests/test_api.py`).
- **mtr is always run with `-n`** (numeric). The app resolves the host itself before the run so the destination hop can be matched by IP, and does reverse DNS afterwards with a cache. Do not pass hostnames to mtr.
- **Simulation mode** (`MTR_TRACKER_SIMULATE=1`, or automatic when the mtr binary is missing) generates synthetic paths seeded by the destination /24. Use it for UI work; it sends no packets.
- **Timestamps** are stored as epoch floats (REAL) and serialised as ISO 8601 UTC strings with a `Z` suffix in the API.
- **Run status**: `runs.status` is `ok` or `error`; `runs.reached` says whether the final hop was the destination. Target `last_status` is `pending | up | degraded | down`; the UI derives `paused` from `enabled = 0`.
- **Route change detection** treats unknown hops (`???`) as wildcards (`routes_equivalent`). A run with a different destination IP is reported as a route change with a "now resolves to" message.
- **Series endpoint** returns raw runs up to `max_points`, otherwise buckets server-side; the chart handles both (`bucket_sec` in the response).
- **Charts** use explicit pixel sizes via the `Sized` ResizeObserver wrapper in `Charts.tsx`; Recharts' ResponsiveContainer and `responsive` prop are intentionally not used.
- **CSS**: custom classes (`.card`, `.btn`, `.input`, `.table`, `.seg`) live in `@layer components` in `index.css` so Tailwind utilities can override them. Theme tokens are CSS variables on `:root` (light), `[data-theme="dark"]` and `[data-theme="oled"]` (true black), mapped with `@theme inline`; the `dark` Tailwind variant matches both dark themes. Themes are listed in `THEMES` in `hooks.ts`, persisted in `localStorage` under `mtr-tracker.theme`, and applied before first paint by the inline script in `index.html`.
- **Notifications**: add a channel by extending `notify.dispatch_event`, `DEFAULT_SETTINGS` in `db.py`, `SettingsUpdate` in `models.py`, the `Settings` interface in `api.ts` and the Settings page. Delivery errors raise `NotifyError` (surfaced by `POST /api/notifications/test`, logged by the scheduler). Tests mock HTTP by setting `notify._TRANSPORT` to an `httpx.MockTransport`.
- **Raw sockets**: real probing needs `CAP_NET_RAW` (compose grants it) or `setcap cap_net_raw+ep $(which mtr)` outside Docker.
- **Screenshots** in `docs/` are taken with headless Chromium against simulation mode; regenerate them if the UI changes materially.
