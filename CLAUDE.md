# MTR Tracker

Continuous MTR monitoring for IT professionals: a FastAPI + SQLite backend runs `mtr --json` against user-defined targets on a schedule, stores every hop of every run, and a React UI visualises latency, loss, jitter, per-hop history and route changes.

## Layout

```
backend/app/        Python package (run with `python -m uvicorn app.main:app` from backend/)
  main.py           FastAPI app factory, lifespan (DB + scheduler), SPA static serving
  api.py            All HTTP routes under /api
  scheduler.py      24/7 loop: due targets -> run -> store -> classify -> events/webhooks; retention purge
  mtr.py            mtr command builder, JSON parser, route signature/compare, simulator
  probes.py         ping / http / tcp / dns probes -> ProbeOutcome (same summary shape as an MTR run + details dict)
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
  components/       Charts (RTT/loss/jitter + Sized wrapper), Visuals (status strip, overview, path profile, histogram, hourly heatmap, route timeline), HopHeatmap, HopTable, PathSummary, RunsTable, EventsList, TargetForm, Layout
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
- **Config is import-time**: `config.py` builds a frozen `Config` when imported. Tests that change env vars reload `config`, `mtr`, `probes`, `scheduler`, `api` and `main` (see `tests/test_api.py`); any new module that imports `config` must join that list.
- **mtr is always run with `-n`** (numeric). The app resolves the host itself before the run so the destination hop can be matched by IP, and does reverse DNS afterwards with a cache. Do not pass hostnames to mtr.
- **Simulation mode** (`MTR_TRACKER_SIMULATE=1`, or automatic when the mtr binary is missing) generates synthetic paths seeded by the destination /24. Use it for UI work; it sends no packets.
- **Timestamps** are stored as epoch floats (REAL) and serialised as ISO 8601 UTC strings with a `Z` suffix in the API.
- **Probe types**: `targets.type` is `mtr | ping | http | tcp | dns`; type-specific settings live in `targets.options` (JSON) validated by the `*Options` models in `models.py` (`validate_options`). Non-MTR runs have `hop_count = 0`, no hop rows, and a `runs.details` JSON blob rendered by `CheckDetails.tsx`. The UI hides path panels via `isPathProbe(type)`. Adding a type: runner in `probes.py` (+ `RUNNERS`), options model, `DEFAULT_OPTIONS`/`PROBE_TYPE_LABEL` in `api.ts`, form fields in `TargetForm.tsx`, details rendering.
- **API token**: `MTR_TRACKER_API_TOKEN` enables a middleware in `main.py` that requires a bearer token on `/api` writes; reads stay open. The client stores it in `localStorage` (`mtr-tracker.token`) and a 401 dispatches `AUTH_REQUIRED_EVENT`, which the layout turns into a prompt.
- **Run status**: `runs.status` is `ok` or `error`; `runs.reached` says whether the final hop was the destination. Target `last_status` is `pending | up | degraded | down`; the UI derives `paused` from `enabled = 0`.
- **Scheduling** is fixed-cadence: `next_run_at = started_at + interval_sec`, set when a run is claimed and re-asserted in `finally` (never earlier than now + 1 s). Runs of the same target never overlap because the target id sits in `Scheduler._running`. A 10-probe mtr run takes ~15 s (cycles + ~5 s trailing wait), so the form warns when probes × probe interval + 5 s does not fit the interval.
- **Route change detection** treats unknown hops (`???`) as wildcards (`routes_equivalent`). A run with a different destination IP is reported as a route change with a "now resolves to" message.
- **Series endpoint** returns raw runs up to `max_points`, otherwise buckets server-side; the chart handles both (`bucket_sec` in the response).
- **Time axes** on every time-based visual (RTT chart, overview, route strip) start at `max(range start, first data point)` so a fresh target is readable; keep new visuals consistent with that rule. The hour-by-day heatmap receives UTC hour buckets and folds them into the browser's local time.
- **Charts** use explicit pixel sizes via the `Sized` ResizeObserver wrapper in `Charts.tsx`; Recharts' ResponsiveContainer and `responsive` prop are intentionally not used.
- **CSS**: custom classes (`.card`, `.btn`, `.input`, `.table`, `.seg`) live in `@layer components` in `index.css` so Tailwind utilities can override them. Theme tokens are CSS variables on `:root` (light), `[data-theme="dark"]` and `[data-theme="oled"]` (true black), mapped with `@theme inline`; the `dark` Tailwind variant matches both dark themes. Themes are listed in `THEMES` in `hooks.ts`, persisted in `localStorage` under `mtr-tracker.theme`, and applied before first paint by the inline script in `index.html`.
- **Notifications**: add a channel by extending `notify.dispatch_event`, `DEFAULT_SETTINGS` in `db.py`, `SettingsUpdate` in `models.py`, the `Settings` interface in `api.ts` and the Settings page. Delivery errors raise `NotifyError` (surfaced by `POST /api/notifications/test`, logged by the scheduler). Tests mock HTTP by setting `notify._TRANSPORT` to an `httpx.MockTransport`.
- **Raw sockets**: real probing needs `CAP_NET_RAW` (compose grants it) or `setcap cap_net_raw+ep $(which mtr)` outside Docker.
- **Screenshots** in `docs/` are taken with headless Chromium against simulation mode; regenerate them if the UI changes materially.
