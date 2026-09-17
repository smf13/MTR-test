# Interface screenshots

These captures show the current MTR Tracker interface. They were taken with headless Chromium against the application running in simulation mode (`MTR_TRACKER_SIMULATE=1`) with a week of seeded history: ten targets covering every probe type, scripted outages, loss and latency incidents and route changes. Use the [interface guide](interface.md) for navigation and controls, or return to the [README](../README.md).

Desktop captures use a 1440 px wide viewport and the dark theme unless stated otherwise; phone captures use a 390 px wide viewport.

## Dashboard

Health summary, the **Latency across targets** comparison chart, filters and target cards.

![Dashboard with health summary, latency comparison chart and target cards](dashboard.png)

The **Table** view of the same targets.

![Dashboard table view](dashboard-table.png)

## Target page

Header, range selector, current and range statistics, the 24-hour status strip and the time-series charts. The dashed line on the latency chart marks a route change; the route timeline below it shows which path was in use when.

![Target page with statistics, latency chart, route timeline, loss and jitter](target.png)

Path profile, latency distribution with percentile markers and the hour-by-day heatmap.

![Path profile, latency distribution and hourly heatmap](target-visuals.png)

### Data tabs

**Current path**: the complete hop table of the latest run with all fourteen columns, and the text report download.

![Current path hop table of the latest run](current-path.png)

**Path history**: one row per hop and one column per run, here coloured by latency. Hot columns are the latency incidents.

![Path history heatmap](path-history.png)

**Path summary**: per-hop statistics over the range with an alternate address expanded at hop 4.

![Path summary with an alternate address expanded](path-summary.png)

**Runs**: paginated run history with result filters. The two most recent runs of this target failed to reach the destination.

![Runs tab](runs.png)

## Other probe types

An HTTP(S) target: response time per run, failed checks, the latest response and the certificate recorded with it.

![HTTP(S) target with latest check and certificate details](http-check.png)

A Globalping HTTP measurement from three remote probes, each listed with its result.

![Globalping HTTP target with per-probe results](globalping-check.png)

## Events and target form

![Events page](events.png)

![Add target form](target-form.png)

## Themes

Light theme on a target page and the OLED (true black) theme on the dashboard.

![Target page in the light theme](target-light.png)

![Dashboard in the OLED theme](dashboard-oled.png)

## Phone layout

| Dashboard | Target page |
| --- | --- |
| ![Dashboard on a phone](mobile-dashboard.png) | ![Target page on a phone](mobile-target.png) |

## Regenerating these captures

1. Build the frontend (`npm run build` in `frontend/`) and start the backend in simulation mode with an empty data directory, using IP-literal or fictional hosts, reverse DNS disabled and notification channels disabled.
2. Seed history rather than waiting for it: call `Scheduler._execute` for each target at backdated timestamps (patch `time.time`), so the rows are exactly what the scheduler writes. Park `next_run_at` in the future afterwards so the live scheduler does not add runs while capturing.
3. Capture with headless Chromium (Playwright) at 1440 px wide, clipping each section to its card, and again at 390 px wide for the phone layout. Move the pointer away from charts before capturing so no tooltip is open.
4. Include the dashboard, a target page with the full Current path table, Path history, Path summary with alternate addresses expanded, Runs, a non-path probe, the events page and all three themes. Check each image against the current interface before replacing the files.
