# MTR Tracker

**Continuous MTR monitoring for IT professionals.** MTR Tracker runs `mtr` against the hosts you care about on a schedule you choose, stores every hop of every run, and gives you a clean web UI to see exactly where latency, packet loss and route changes happen over time.

Think of it as SmokePing or Uptime Kuma, but built around the full MTR path rather than a single ping.

![Dashboard](docs/dashboard.png)

| Target detail | Path history heatmap |
| --- | --- |
| ![Target detail](docs/target.png) | ![Path history](docs/path-history.png) |

## Features

- **Scheduled MTR runs** per target (10 seconds to 24 hours), with configurable probe count, probe interval, packet size, max hops, IPv4/IPv6, and ICMP / UDP / TCP probing with a port.
- **Every hop, every run.** Loss %, sent/received, last/avg/best/worst, standard deviation, jitter (Jttr, Javg, Jmax, Jint), ASN and reverse DNS for each hop are stored and searchable.
- **Time-series views.** Round-trip time with best–worst band, packet loss and jitter charts over 1h to 30d, automatically aggregated for long ranges. Click a point to open the underlying run.
- **Path history heatmap.** Hop-by-run grid coloured by loss, latency or jitter, so a flapping hop or a mid-path degradation is obvious at a glance.
- **Path summary.** Per-hop statistics aggregated over the selected range, including alternate addresses seen at each hop (ECMP or reroutes) with how often each was observed.
- **Route change detection** with a hop-by-hop diff, and detection of destination IP changes for DNS-based targets.
- **Alerting.** Per-target loss and latency thresholds produce up / degraded / down state transitions, an event log, and optional JSON webhooks (works with n8n, Zapier, custom receivers).
- **Quick trace.** Run a one-off MTR from the server without saving it, then add the host as a target in one click.
- **Text report export** of any run in the familiar `mtr --report` layout.
- **Retention** control, SQLite storage (WAL mode), light and dark themes, responsive layout for phones and wall displays.
- **Simulation mode** to demo or develop without raw-socket privileges.

## Quick start (Docker)

```bash
git clone https://github.com/smf13/MTR-test.git mtr-tracker
cd mtr-tracker
docker compose up -d --build
```

Open <http://localhost:8080>, click **Add target**, enter a host and an interval, and the first run starts immediately.

Data lives in the `mtr-tracker-data` volume (`/data` inside the container). The container needs `CAP_NET_RAW` for mtr, which `docker-compose.yml` already grants. Uncomment `network_mode: host` if you want the first hop to be your host's real gateway instead of the Docker bridge.

### Configuration

Environment variables (read at startup):

| Variable | Default | Purpose |
| --- | --- | --- |
| `MTR_TRACKER_PORT` | `8080` | HTTP port |
| `MTR_TRACKER_DATA_DIR` | `/data` | Directory for the SQLite database |
| `MTR_TRACKER_MAX_CONCURRENT_RUNS` | `4` | How many mtr processes may run at once |
| `MTR_TRACKER_MTR_BINARY` | `mtr` | Path to the mtr binary |
| `MTR_TRACKER_SIMULATE` | `0` | `1` generates synthetic paths instead of sending packets |
| `MTR_TRACKER_LOG_LEVEL` | `info` | Log verbosity |

Everything else (retention days, reverse DNS, ASN lookup, webhook URL and events) is set in the UI under **Settings** and stored in the database.

### Webhook payload

```json
{
  "source": "MTR Tracker",
  "event": "down",
  "severity": "critical",
  "message": "Head office WAN is DOWN: destination unreachable",
  "target": { "id": 3, "name": "Head office WAN", "host": "203.0.113.1" },
  "run_id": 1842,
  "details": { "previous": "up", "current": "down", "loss_pct": 100.0 },
  "timestamp": 1758000000.0
}
```

Event kinds: `down`, `recovered`, `degraded`, `route_change`.

## How a run works

1. The scheduler wakes every second and launches any enabled target whose next run is due (limited by `MTR_TRACKER_MAX_CONCURRENT_RUNS`).
2. The host is resolved to a single IP (honouring the target's IP version) so the destination hop can be identified unambiguously.
3. `mtr --json -n -c <count> -i <probe interval> -s <size> -m <max hops> -o LSDRNBAWVGJMXI [-4|-6] [--udp|--tcp -P <port>] [-z] <ip>` runs and its JSON report is parsed.
4. Hop IPs are reverse-resolved (cached), the route signature is compared with the previous run, thresholds are evaluated, and the run, hops and any events are written in one transaction.
5. State transitions (`up` → `degraded` → `down` → `recovered`) create events and fire webhooks.

## API

The UI is a thin client over a JSON API, documented live at `/api/docs`.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/status` | Engine status, counters, mtr version |
| `GET` / `PUT` | `/api/settings` | Global settings |
| `GET` / `POST` | `/api/targets` | List (with 24h stats and sparkline) / create |
| `GET` / `PUT` / `DELETE` | `/api/targets/{id}` | Detail with range stats (`?range=24h`) / update / delete |
| `POST` | `/api/targets/{id}/run` | Run now |
| `GET` | `/api/targets/{id}/runs` | Paginated runs (`limit`, `offset`, `range`, `status=ok|failed|route_change`) |
| `GET` | `/api/targets/{id}/series` | Destination latency / loss / jitter time series, auto-bucketed |
| `GET` | `/api/targets/{id}/hops/history` | Hop-by-run matrix for the heatmap |
| `GET` | `/api/targets/{id}/hops/summary` | Per-hop aggregates with alternate addresses |
| `GET` | `/api/targets/{id}/events` | Events for one target |
| `GET` | `/api/runs/{id}` | A run with all hops |
| `GET` | `/api/runs/{id}/report` | Plain-text mtr-style report |
| `GET` / `DELETE` | `/api/events` | Global event log (`kind`, `severity`, `target_id`, `range`) |
| `POST` | `/api/probe` | One-off trace, not stored |

Ranges accept `1h`, `6h`, `24h`, `7d`, `30d` or a number of seconds.

## Development

Backend (Python 3.11+):

```bash
cd backend
pip install -r requirements-dev.txt
MTR_TRACKER_SIMULATE=1 MTR_TRACKER_DATA_DIR=./data python -m uvicorn app.main:app --reload --port 8080
python -m pytest
```

Frontend (Node 22):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to :8080
npm run build      # writes dist/, which the backend serves automatically
```

Running real probes outside Docker requires the `mtr` binary (`apt install mtr-tiny`) and either root or `setcap cap_net_raw+ep $(which mtr)`.

## Project layout

```
backend/app/
  main.py        FastAPI app, static file serving, lifespan
  api.py         HTTP routes
  scheduler.py   24/7 run loop, state transitions, retention
  mtr.py         mtr command builder, JSON parser, simulator
  resolver.py    forward / reverse DNS with cache
  db.py          SQLite schema and helpers
  models.py      request schemas
frontend/src/
  pages/         Dashboard, TargetDetail, RunView, Events, Settings, QuickTrace
  components/    charts, heatmap, hop tables, forms, layout
```

## License

MIT
