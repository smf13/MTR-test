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
  globalping.py     Globalping API client (globalping.io): remote ping/dns/http -> ProbeOutcome, remote mtr/traceroute -> MtrResult with hops
  notify.py         Notification channels (webhook, Pushover); dispatch_event fans one payload out to all enabled channels
  resolver.py       forward DNS and reverse DNS with a TTL cache
  db.py             SQLite schema (targets, runs, hops, events, settings), transaction() helper, batched retention purge
  models.py         Pydantic request schemas
  config.py         Environment variables (MTR_TRACKER_*), evaluated at import time
backend/tests/      pytest (asyncio mode auto); fixtures in conftest.py (client, protected_client, static_client) built by helpers.app_client, which runs the app in simulation mode
frontend/src/       React 19 + TypeScript + Vite 8 + Tailwind 4 + Recharts 3
  api.ts            Typed API client and shared interfaces
  utils.ts          Formatting, colour scales, status helpers
  hooks.ts          usePoll (visibility-aware polling), useTheme, useLocalStorage
  pages/            Dashboard, TargetDetail, RunView, Events, Settings, QuickTrace
  components/       Charts (RTT/loss/jitter + Sized wrapper), Visuals (overview, path profile, histogram, hourly heatmap, route timeline), StatusStrip, HopHeatmap, HopTable, PathSummary, RunsTable, EventsList, TargetForm, TargetActions, Popover (HelpTip/ActionMenu), Tabs, Tags (colour provider, chips, picker), TypeBadge, Pager, Layout
frontend/tests/     Vitest + jsdom + Testing Library component tests; setup.ts isolates localStorage, mocks ResizeObserver and rejects unexpected fetch calls
docs/interface.md   Current UI navigation and control guide; docs/screenshots.md labels the older captures as archived
Dockerfile          Multi-stage: node build of frontend -> python:3.12-slim with mtr-tiny + iputils-ping + tini; unprivileged "mtr" user
docker-entrypoint.sh  Runs as root only to chown /data, then setpriv to the mtr user (MTR_TRACKER_RUN_AS_ROOT=1 opts out)
docker-compose.yml  Grants NET_RAW, persistent /data volume; the image's HEALTHCHECK on /healthz applies
```

## Commands

```bash
# Run each section from the repository root; backend and frontend servers use separate terminals.
# Backend (Python 3.11+; CI/Docker use 3.12)
cd backend && pip install -r requirements-dev.txt
MTR_TRACKER_SIMULATE=1 MTR_TRACKER_DATA_DIR=./data python -m uvicorn app.main:app --reload --port 8899
python -m pytest -q

# Frontend (Node 22.22.2+ in the 22.x line, required by the locked jsdom version)
cd frontend && npm ci
npm run dev          # Vite on :5173, proxies /api to :8899
npm run build        # tsc --noEmit && vite build -> frontend/dist (served by the backend if present)
npm run typecheck    # TypeScript check without emitting a build
npm test             # Vitest component tests; no running backend needed

# Docker
docker compose up -d --build
```

## Conventions and gotchas

- **Naming**: the product is "MTR Tracker". Identifiers use `mtr-tracker` (logger names, image, volume) and env vars use the `MTR_TRACKER_` prefix. Do not reintroduce other names.
- **Config is import-time**: `config.py` builds a frozen `Config` when imported. Tests that change env vars reload `config`, `mtr`, `probes`, `globalping`, `scheduler`, `api` and `main` (see `tests/helpers.py`); any new module that imports `config` must join that list.
- **mtr is always run with `-n`** (numeric). The app resolves the host itself before the run so the destination hop can be matched by IP, and does reverse DNS afterwards with a cache. Do not pass hostnames to mtr.
- **Simulation mode** (`MTR_TRACKER_SIMULATE=1`) produces synthetic probe results; local MTR paths are seeded by the destination /24. Missing mtr or ping binaries trigger fallback only for their respective probe types. Hostname resolution for local MTR/ping/TCP, reverse DNS for local MTR and enabled notifications can still access the network. For isolated UI work, use IP-literal targets, disable reverse DNS and leave notification channels disabled.
- **Timestamps** are stored as epoch floats (REAL) and serialised as ISO 8601 UTC strings with a `Z` suffix in the API.
- **Probe types**: `targets.type` is `mtr | ping | http | tcp | dns | globalping`; type-specific settings live in `targets.options` (JSON) validated by the `*Options` models in `models.py` (`validate_options`). Summary runs have `hop_count = 0`, no hop rows, and a `runs.details` JSON blob rendered by `CheckDetails.tsx`. The UI decides on path panels with `isPathProbe(type, options)` and on loss semantics with `isPacketProbe(type, options)`, because a Globalping target is a path probe only when its `measurement` is `mtr` or `traceroute`. Adding a type: runner in `probes.py` (+ `RUNNERS`; `run_probe(t, settings)` also receives the settings), options model, `DEFAULT_OPTIONS`/`PROBE_TYPE_LABEL`/`LATENCY_ALERT_DEFAULT` in `api.ts`, form fields in `TargetForm.tsx`, details rendering.
- **Globalping** (`globalping.py`) supports all five API measurements. `build_request` maps the target (`count` = packets for ping/mtr, max 16; `protocol`/`port`/`ip_version` for mtr/traceroute; `port` for http) and its options (`measurement`, `location` as the API's "magic" field, `probes`; dns: `record_type`/`resolver`/`expected`; http: `path`/`http_method`/`http_protocol`/`expected_status`/`keyword`, a pasted URL is split by `http_target`) to `POST /v1/measurements`; `measure()` polls until finished and raises `GlobalpingError` (rate limit, no probes, validation, timeout). `PATH_MEASUREMENTS` (mtr, traceroute) become an `MtrResult` (hops via `hop_from_result` / `hop_from_traceroute`, `src` = probe label, `details.probe`) that the scheduler runs through `_execute_path` with `local_names=False` (the probe's hostnames are kept); ping becomes a packet-based `ProbeOutcome` (`_ping_outcome`); dns and http are per-probe checks folded by `_check_outcome` (down when no probe passed, a warning and therefore degraded when only some did), with `details.probes[]`, `details.answers`/`rcode` for dns and `details.request`/`status`/`timings`/`tls` for http (`tls_info_from_result` maps the API's tls block to the same shape as the local probe's `details.tls`). The optional `settings.globalping_token` is a masked secret. Simulation mode fabricates probes without calling the API; tests use `globalping._FORCE_LIVE` + `globalping._TRANSPORT` (MockTransport). The UI derives path/packet semantics from `isPathProbe`/`isPacketProbe`/`latencyLabel` in `utils.ts`, all of which take the options.
- **DNS `random_prefix`** queries `<random label>.<name>`; NXDOMAIN/NoAnswer count as a successful lookup when no expected answer is set. **HTTP `tls_info`** stores `details.tls` (`certificate_details()` from the response's ssl object, or a dedicated connection as fallback); `tls_expires_in_days`/`tls_warn_days` stay alongside for the warning.
- **API token**: `MTR_TRACKER_API_TOKEN` enables a middleware in `main.py` that requires a bearer token on `/api` writes; reads stay open. `api.is_authenticated(request)` is the single check (bytes compare, so non-ASCII headers give 401 not 500); with a token configured, `GET /api/settings` returns `redact_settings()` output to unauthenticated callers (mask `********`, last four characters kept for credentials, path and query hidden for the webhook URL) and `GET /api/status` omits `db_path`. `PUT /api/settings` and the notification test run the patch through `strip_masked()` so an echoed mask never overwrites a stored secret. The client stores the token in `localStorage` (`mtr-tracker.token`), sends it on every request, and a 401 dispatches `AUTH_REQUIRED_EVENT`, which the layout turns into a prompt.
- **Unknown `/api/...` paths** return a JSON 404 from the SPA catch-all in `main.py`; never let them fall through to `index.html`.
- **Writes and deletes**: `update_target` rejects explicit nulls with 422 (only `port` may be cleared) and re-checks the tcp port. Deleting a target (single, bulk, or import in replace mode) first calls `Scheduler.cancel(ids)` so a run in flight cannot write against a vanished row; the scheduler also tolerates `sqlite3.IntegrityError` for that case. Run + hops are written inside `db.transaction()` (one lock hold, one commit or rollback); use it for any multi-statement write.
- **Scheduler details**: `_execute` dispatches to `_execute_path` (local mtr via `_run_local_mtr`, or Globalping mtr/traceroute) or `_execute_probe` (everything else). Notification deliveries are tasks kept in `Scheduler._notify_tasks` and drained by `stop()` (asyncio only holds weak references); `stop()` also awaits the cancelled loop tasks. Route changes are judged only between runs that both reached the destination. `mtr.py` and `probes.py` kill their child process on `CancelledError`. `mtr.min_probe_interval()` is 1 s unless the process is root (mtr refuses shorter `-i` otherwise); `run_mtr` clamps to it and `/api/status` reports it.
- **Database performance**: `runs(started_at)` and `events(run_id)` are indexed (the latter is what the FK `ON DELETE SET NULL` needs); `purge_older_than` deletes in batches of 5000 with a commit and a yield between them; the dashboard sparkline uses a `ROW_NUMBER()` window instead of a correlated subquery. Keep new hot queries index-backed.
- **HTTP probe**: the body is streamed and only `MAX_HTTP_BODY` bytes are kept (`details.truncated` marks a cut); the certificate expiry is read from the response's own TLS connection (`cert_days_from_response`) and only checked when `tls_warn_days > 0`; `details.tls_warn_days` records the threshold the run was judged by. `ping_payload_bytes` makes ping send the same total packet size as mtr. `/api/probe` is limited by `_adhoc_slots` (2 concurrent, 429 after 30 s).
- **Run status**: `runs.status` is `ok` or `error`; `runs.reached` says whether the final hop was the destination. Target `last_status` is `pending | up | degraded | down`; the UI derives `paused` from `enabled = 0`.
- **Scheduling** is fixed-cadence: `next_run_at = started_at + interval_sec`, set when a run is claimed and re-asserted in `finally` (never earlier than now + 1 s). Runs of the same target never overlap because the target id sits in `Scheduler._running`. A 10-probe mtr run takes ~15 s (cycles + ~5 s trailing wait), so the form warns when probes × probe interval + 5 s does not fit the interval.
- **Route change detection** treats unknown hops (`???`) as wildcards (`routes_equivalent`). A run with a different destination IP is reported as a route change with a "now resolves to" message.
- **Series endpoint** returns raw runs up to `max_points`, otherwise buckets server-side; the chart handles both (`bucket_sec` in the response).
- **Time axes** on every time-based visual (RTT chart, overview, route strip) start at `max(range start, first data point)` so a fresh target is readable; keep new visuals consistent with that rule. The hour-by-day heatmap receives UTC hour buckets and folds them into the browser's local time.
- **Charts** use explicit pixel sizes via the `Sized` ResizeObserver wrapper in `Charts.tsx`; Recharts' ResponsiveContainer and `responsive` prop are intentionally not used.
- **Dashboard and detail navigation**: Dashboard shows health summaries, filters and target cards/table before the optional comparison chart. `TargetDetail` keeps statistics and charts on one page, including the path profile (or latest check), distribution and hourly heatmap. Below them, one `Tabs` bar contains Current path, Path history, Path summary · [range], Runs and Events. Non-path probes offer Runs and Events, falling back to Runs when a saved path tab is inapplicable. The tab is remembered under `mtr-tracker.tab`; obsolete `mtr-tracker.detail-view` and `mtr-tracker.path-tab` preferences are ignored.
- **Target controls**: `Run now` stays visible; `TargetActions` supplies Pause/Resume, Edit, Clone and Delete through `ActionMenu`. `Popover` portals menus and help to `document.body` so table/card clipping cannot hide them. Preserve Escape/outside-click dismissal and keyboard focus handling; `Tabs` supports Left/Right/Home/End, and action menus support Up/Down/Home/End.
- **Hop tables and legends**: `HopTable` shows all 14 original columns by default, including full hostnames/IPs, ASN, Snt/Rcv, Last/Avg/Best/Wrst, StDev, Jitter/Jmax and the latency bar. The optional `compact` prop omits only ASN, Rcv and Jmax; current path, run detail and quick trace use the full default. There is no column selector and obsolete `mtr-tracker.hop-columns` preferences are ignored. Tables use natural height and horizontal scrolling without truncating hop identities. `HeatLegend` is shared by hop history and the hourly heatmap: loss uses fixed percent stops, latency/jitter use the displayed data's maximum in milliseconds, and no response/no data have distinct markers.
- **Code splitting**: only the Dashboard ships in the main bundle; `App.tsx` lazy-loads the other pages and the Dashboard lazy-loads `OverviewChart`, so Recharts stays out of the first paint. Anything the Dashboard needs without charts must not import `Charts.tsx`/`Visuals.tsx` (that is why `StatusStrip`, `Pager` and `TypeBadge` are separate files).
- **Polling**: `usePoll` keys every request to a generation counter that is bumped when its deps change or the hook unmounts; results from an older generation are discarded and an in-flight request is never reused for new parameters. Keep that behaviour when touching the hook.
- **Runs carry `target_type`** (`/api/runs/{id}` joins the target), so `RunView` never guesses the probe type from the details.
- **CSS**: custom classes (`.card`, `.btn`, `.input`, `.table`, `.seg`) live in `@layer components` in `index.css` so Tailwind utilities can override them. Theme tokens are CSS variables on `:root` (light), `[data-theme="dark"]` and `[data-theme="oled"]` (true black), mapped with `@theme inline`; the `dark` Tailwind variant matches both dark themes. Themes are listed in `THEMES` in `hooks.ts`, persisted in `localStorage` under `mtr-tracker.theme`, and applied before first paint by the inline script in `index.html`.
- **Tags** are a JSON list on the target, sorted case-insensitively on write (`models.sort_tags`) and again on read (`_target_out`), so API output is always sorted. Colours are global, not per target: `settings.tag_colors` maps tag -> `#rrggbb` (validated by `clean_tag_colors`); a tag without an entry gets a deterministic preset from `autoTagColor` in `utils.ts`, which must stay in step with `TAG_PRESETS`. `GET /api/tags` lists tags in use with counts and colours; the UI reads it through `TagColorsProvider` / `useTagColors` in `components/Tags.tsx`, home of `TagChip`, `TagList` and `TagColorPicker`. The target form batches colour picks and writes them via `PUT /api/settings` after the target is saved.
- **Target form**: `TargetForm` initialises its fields only when it opens or when the edited target's id changes (never on a refreshed copy of the same target, which used to wipe typing). It starts from `targetInput(initial)` when editing, or from the defaults plus `prefill` when adding; `cloneInput(t)` (api.ts) is a full `TargetInput` with a "(copy)" name and is what the Clone menu actions (dashboard card/table, target page) pass as `prefill`, together with the optional `title`/`submitLabel` props. On the target page the clone prefill is memoised per clone session because the target object is replaced by every poll.
- **Mobile navigation**: the hamburger panel in `Layout.tsx` is absolutely positioned inside the sticky header (with a click-away backdrop) so it opens at the current scroll position; do not move it back into the document flow.
- **Notifications**: add a channel by extending `notify.dispatch_event`, `DEFAULT_SETTINGS` in `db.py`, `SettingsUpdate` in `models.py`, the `Settings` interface in `api.ts` and the Settings page. Delivery errors raise `NotifyError` (surfaced by `POST /api/notifications/test`, logged by the scheduler). Tests mock HTTP by setting `notify._TRANSPORT` to an `httpx.MockTransport`.
- **Raw sockets**: the container runs as uid 1000; `mtr-packet` and `ping` carry the `cap_net_raw` file capability (Debian sets it, the Dockerfile repeats it) and NET_RAW must be in the container's capability set (compose grants it). Outside Docker, `apt install mtr-tiny iputils-ping` does the same. Root is only required for probe intervals below 1 s.
- **Screenshots** in `docs/` are taken with headless Chromium against simulation mode; regenerate them if the UI changes materially.
- **Validation**: `.github/workflows/ci.yml` runs backend pytest and the frontend build plus `npm test`, then builds the Docker image. Component tests mock API calls and chart sizing; they do not replace visual checks of real charts, scrolling, popover placement and all three themes. Keep README and the interface guide aligned with visible labels and probe-specific navigation.
