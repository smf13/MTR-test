# Interface guide

This guide describes the current MTR Tracker interface. Start with the [README](../README.md) for installation, configuration and API documentation.

## Dashboard

The top summary shows target health counts, mean latency from targets' latest available runs, and route changes over the last 24 hours. Pending appears in the health summary when at least one target has not completed its first run. These totals cover all targets, including when a search filter is active.

The **Latency across targets · 24h** chart sits near the top of the dashboard, directly below the health summary and above the filters and target list. Use **Collapse** or **Expand** to control its visibility. Select a legend label to hide or show that target's series. The comparison uses all targets returned by the overview endpoint; the search field filters the cards/table, not this chart.

Below the chart, search by name, host, description or tag. Choose **Cards** or **Table**, and sort by **Status**, **Name**, **Latency**, **Loss** or **Hops**. Status sorting puts down and degraded targets first. Click or tap a target's name to open it; a card's sparkline also opens the target page.

Cards emphasize the latest measurement and a sparkline that fills the available width. They also show 24-hour availability, a status timeline, the probe schedule and tags. HTTP, TCP and DNS targets show pass/fail and use **Response**, **Connect** or **Lookup** for the main timing metric. Packet-based probes show loss.

## Target actions

The same controls appear on dashboard cards, table rows and target pages:

| Control | Result |
| --- | --- |
| **Run now** | Queue a run immediately. Disabled while that target is already running. |
| Three-dot menu → **Pause** / **Resume** | Disable or enable scheduled checks. Existing history is retained. |
| Three-dot menu → **Edit** | Change the target's settings. |
| Three-dot menu → **Clone** | Open a new-target form with the source settings and a “(copy)” name. Adjust the values and select **Create clone** to save it. |
| Three-dot menu → **Mute notifications** / **Unmute notifications** | Stop or resume delivering this target's events to Pushover and the webhook. Events are still recorded; a muted target shows a **Muted** marker next to its status. The same switch is **Send notifications** in the target form. |
| Three-dot menu → **Delete** | Open a confirmation dialog. Confirming removes the target and its runs, hops and events. |

Cloning copies configuration, not monitoring history. **Add target** starts a new target from the default settings.

## Target pages

The time-range selector offers **1h**, **6h**, **24h**, **7d** and **30d**. It controls historical statistics, charts, runs and events. The latest measurement/current path always comes from the newest run, and the status strip always covers the last 24 hours.

Statistics and charts stay together on one page: current and range statistics, the 24-hour status strip, latency/loss charts, latency distribution and the hour-by-day heatmap. Packet probes also show jitter. Path probes show a route timeline and **Path profile**; other probes show **Latest check** details.

The data tabs sit below the charts. Switching between them keeps the charts visible above.

| Data tab | What it contains | Available for |
| --- | --- | --- |
| **Current path** | The latest run's complete hop table and a **Text report** download. The destination row is highlighted and carries a **dst** marker. | Local MTR and Globalping MTR/traceroute |
| **Path history** | One row per hop and one column per run. Choose **Loss**, **Latency** or **Jitter**; select a column to open that run. | Local MTR and Globalping MTR/traceroute |
| **Path summary · [range]** | Aggregated per-hop statistics, including ASN, address frequency, average/maximum loss, latency, standard deviation and jitter. Expand a hop to inspect alternate addresses seen at that position. Runs in which the hop answered no probe appear as **n silent** under **Seen** and count as loss on the usual address; they are not alternates. | Local MTR and Globalping MTR/traceroute |
| **Runs** | Paginated run history with **All**, **Reached** or **Passed**, **Failed**, and **Route changes** where applicable. Select a run to inspect it. | Every target |
| **Events** | This target's events in the selected range. The tab displays a count when events are present. | Every target |

**Current path** is the initial data tab for path probes; other probes open **Runs**. The browser remembers the selected tab. When a remembered path tab does not apply to the next target, the page shows **Runs** instead.

**Path profile**, above the data tabs, compares per-hop latency and loss. Switch between **Latest run** and **Avg · [range]**. Intermediate-hop loss alone does not establish loss at the destination.

### Path map

With a MaxMind licence key saved under **Settings → MaxMind GeoIP**, a **Path map** card sits between the latency distribution and the hour-by-day heatmap (**Location map** on ping, HTTP, TCP and DNS targets). It plots the latest completed run: the monitoring server in the accent colour, hops in grey and the destination in green (red when it did not respond), joined by a dashed line in path order. Consecutive hops in the same place share one marker; hover a marker for its hop numbers, select it for host names, addresses, latency and loss. Use the **+**/**−** controls or pinch to zoom; the mouse wheel keeps scrolling the page. The caption counts how many hops were located, and a line under the map lists the hops that were not, with the reason (private address, not in database). Without a key, the card is replaced by a one-line pointer to Settings; while the database is still downloading, the card says so.

### Hop table columns

Current path, individual run pages and quick trace show the complete hop table immediately. The original 14 columns are always available, without a column-mode selector.

| Column | Meaning |
| --- | --- |
| **#** | Hop number |
| **Host** | Full hostname and IP address, or the IP when no hostname is available |
| **ASN** | Autonomous system number, when available |
| **Loss** | Packet loss percentage |
| **Snt / Rcv** | Packets sent and received |
| **Last / Avg / Best / Wrst** | Last, average, best and worst round-trip latency |
| **StDev** | Latency standard deviation |
| **Jitter / Jmax** | Average and maximum jitter |
| **Latency** | Best-to-worst latency bar with an average marker |

Timing columns use milliseconds and loss uses percent. Tables show their full height; scroll horizontally to reach additional columns on narrow screens. Hostnames and IP addresses are not truncated. “No response” means a hop did not report an address. A dash means the requested measurement is unavailable. A previously saved compact-view preference does not hide any columns.

## Charts, scales and help

The latency chart's line represents average latency and its band spans best to worst. Dashed markers identify route changes. Select a point to open its associated run. The **Route in use** strip under the chart colours each stretch of time by the path in use; a hop that answered nothing in one run is treated as a wildcard, so rate-limited routers do not create extra routes or split a segment. Hover a route's label to see whether such runs were folded into it. Long ranges may use bucketed averages; read the caption to see the aggregation interval. The latency histogram uses those same series points, so a long-range histogram can describe bucket averages rather than individual runs.

Both heatmaps show numeric legends:

- **Loss** uses fixed percentage stops at 0, 2, 10, 40 and 100.
- **Latency** and **Jitter** use a sequential colour scale from zero to the maximum in the displayed data, labelled in milliseconds. Check the numeric scale when comparing different targets or ranges.
- In latency/jitter views, red identifies no response. In the hourly heatmap, a red cell means the hour contains runs but no successful responses. Neutral cells indicate no data; in path history this also includes a hop position absent from a run.

The **Hour by day** grid uses the browser's local time. Chart timestamps also use local time. Hover tooltips provide details where supported; on a phone, use the visible legends and run detail pages for precise values.

Select an information icon beside a chart heading to open its explanation. Help works by click, tap or keyboard, and closes with **Close help**, **Escape**, or a click/tap outside it.

## Phones, themes and keyboard controls

On narrow screens, open **Menu** in the sticky header for **Dashboard**, **Events**, **Quick trace** and **Settings**. The **Switch theme** button cycles **Dark → OLED → Light**. On desktop, choose a theme at the bottom of the sidebar. The choice is remembered in this browser.

Target tabs and wide data tables scroll horizontally when needed. The three-dot action menu opens over the page so it remains visible inside a scrolling table.

With a keyboard, use Left/Right or Home/End in the data tabs. In an action menu, use Up/Down or Home/End to select an item, Enter to activate it, and Escape to close it and return focus to the menu button.

## Settings and saved preferences

**Settings** controls retention, name/ASN lookups, notification channels, public URL, Globalping credentials, MaxMind GeoIP credentials and tag colours. The **MaxMind GeoIP** section shows whether the GeoLite2 City database is present, its build date and the last download error, and offers **Download now** once a licence key is saved. It also offers **Export JSON** and **Import JSON** for target definitions; these exports do not contain monitoring history. Import modes update by name, always create, or replace all targets.

Theme, dashboard view/sort, comparison-chart visibility, data tab and time range are saved in browser storage. They are preferences for that browser, not global settings.

If the server requires an API token, enter it under **Settings → API access** or in the prompt after an unauthorized write. Save the token, then retry the action. The token is stored in this browser; reading the dashboard does not require it.
