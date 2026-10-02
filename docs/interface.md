# Interface guide

This guide describes the current MTR Tracker interface. Start with the [README](../README.md) for installation, configuration and API documentation.

## Dashboard

The top summary shows target health counts, mean and median latency from targets' latest available runs, and route changes over the last 24 hours. The median is the middle value across targets, so one slow target moves the mean but not the median. Pending appears in the health summary when at least one target has not completed its first run. These totals cover all targets, including when a search filter is active.

The **Latency across targets · 24h** chart sits near the top of the dashboard, directly below the health summary and above the filters and target list. Use **Collapse** or **Expand** to control its visibility. Select a legend label to hide or show that target's series. Every line has its own look: the first eight targets (in name order) get eight distinct colours, chosen to stay apart for colour-blind readers too, and further targets repeat those colours as dashed, then dotted lines; the legend and the hover tooltip show each line's sample. Hiding a series keeps the other colours. The comparison uses all targets returned by the overview endpoint; the search field filters the cards/table, not this chart.

Below the chart, search by name, host, description, group or tag. Choose **Cards** or **Table**, and sort by **Status**, **Name**, **Latency**, **Loss** or **Hops**. Status sorting puts down and degraded targets first. Click or tap a target's name to open it; a card's sparkline also opens the target page.

**Groups.** Once a target has a **Group** (chosen in the target form or in the target page's header, see below), the cards and the table are split into one collapsible section per group, in alphabetical order, with targets without a group last under **Ungrouped**. Each heading is a button showing the group's name, how many targets it holds and how many of them are down, degraded, pending, up or paused, with a dot in the colour of the worst state, so a collapsed group still tells you whether it needs attention. Select it (or press Enter or Space on it) to collapse or expand the group; **Collapse all** / **Expand all** next to **Cards** and **Table** does every group at once. Which groups are collapsed is remembered in this browser. While the search field holds text, matching targets are shown even inside collapsed groups. Sorting applies within each group. Without any group the dashboard shows one plain list as before.

**Choosing a group.** The target form's **Group (optional)** field is a dropdown: **No group**, every group already in use, and **New group…**, which opens a **New group name** field underneath; the new group exists as soon as the target is saved. The target page's header carries the same dropdown (labelled **Group**, with a folder icon, next to the probe type), and there a choice is saved at once; **New group…** turns it into a name field with **Save** and **Cancel** (Enter saves, Escape cancels).

**Moving several targets.** Select **Select** next to **Collapse all** to enter selection mode (the button then reads **Done**). Every card and table row gains a checkbox, and every group heading one that ticks or clears the whole group (it shows a dash when only part of the group is ticked); the table's header checkbox ticks every target shown. The **Selected targets** bar stays under the top of the screen while you scroll: it counts the selection and offers **Select all shown**, **Clear** and the **Move to group…** dropdown with every group, **Remove from group** and **New group…** (a name field with **Save** and **Cancel**). Picking a group moves the targets at once, clears the selection and opens the destination group if it was collapsed. **Done** leaves selection mode.

**Renaming a group.** The pencil at the right of a group's heading (**Rename group …**; Ungrouped has none) opens **Rename group** with a **New name** field. Every target in the group moves to the new name. If another group already has that name, the dialog says so and the button reads **Merge groups**: the two become one. A collapsed group stays collapsed under its new name.

Cards emphasize the latest measurement and a sparkline that fills the available width. Under the latest measurement, two small lines give the **24h avg** and **24h median** of the runs that reached the destination over the last 24 hours; the median is the typical value, so a few slow runs move the average but not the median. Cards also show 24-hour availability, a status timeline, the probe schedule and tags. HTTP, TCP and DNS targets show pass/fail and use **Response**, **Connect** or **Lookup** for the main timing metric. Packet-based probes show loss.

## Target actions

The same controls appear on dashboard cards, table rows and target pages:

| Control | Result |
| --- | --- |
| **Run now** | Queue a run immediately. Disabled while that target is already running. |
| Three-dot menu → **Pause** / **Resume** | Disable or enable scheduled checks. Existing history is retained. |
| Three-dot menu → **Edit** | Change the target's settings. |
| Three-dot menu → **Clone** | Open a new-target form with the source settings and a “(copy)” name. Adjust the values and select **Create clone** to save it. |
| Three-dot menu → **Mute notifications** / **Unmute notifications** | Stop or resume delivering this target's events to Pushover and the webhook. Events are still recorded; a muted target shows a **Muted** marker next to its status. The same switch is **Send notifications** in the target form. |
| Three-dot menu → **Clear history…** (target page only) | Open a dialog that deletes **Failed runs only** (runs with an error or a failed check, and the events they raised; the default, for clearing out runs from a target's set-up) or the **Entire history** (every run and event; the configuration stays). Each choice shows how many runs it removes, and the button names the count (**Delete 3 runs**); it is disabled when there is nothing to delete. When the latest run is among the deleted, the status shows pending until the next run. |
| Three-dot menu → **Delete** | Open a confirmation dialog. Confirming removes the target and its runs, hops and events. |

Cloning copies configuration, not monitoring history. **Add target** starts a new target from the default settings.

### DNS check settings

A **DNS** target asks one resolver for one record per run. **Transport** picks how it is asked:

| Transport | Resolver | Port |
| --- | --- | --- |
| **UDP (port 53)** | Optional; empty uses the monitoring server's own resolver. A truncated answer is retried over TCP, and the run details say so. | 53, or **Resolver port** |
| **TCP (port 53)** | Optional, as for UDP. | 53, or **Resolver port** |
| **DNS over TLS (DoT, port 853)** | Required: a host name or IP. | 853, or **Resolver port** |
| **DNS over HTTPS (DoH)** | Required: a host name or IP (the standard `/dns-query` path is added) or a full `https://` URL for any other path or port. | From the URL, 443 by default |

To set up encrypted DNS, select **Add target**, choose **DNS** as the probe type, enter the **Name to resolve** (for example `example.com`), then pick **DNS over TLS (DoT, port 853)** or **DNS over HTTPS (DoH)** under **Transport**. A help line under **Transport** says what the chosen transport does. For DoT and DoH, the **Public:** buttons under **Resolver** (**Cloudflare**, **Google**, **Quad9**) fill in that provider's resolver for the chosen transport (`one.one.one.one` / `https://cloudflare-dns.com/dns-query`, `dns.google` / `https://dns.google/dns-query`, `dns.quad9.net` / `https://dns.quad9.net/dns-query`); hovering a button shows the value, and the button of the resolver in the field shows as pressed. Any other resolver can be typed in.

For DoT and DoH, **Verify the resolver's certificate** (on by default) checks the certificate against the resolver's host name, or its IP when an address is given. Turn it off only for internal resolvers with private certificates. The form refuses DoT or DoH without a resolver. A run's details list the **Resolver**, the address that answered and the **Transport**, with the port when it is not the standard one.

A **Globalping** DNS measurement offers **Transport** too, limited to **UDP (port 53)** and **TCP (port 53)**: the remote probes speak plain DNS only.

### HTTP keyword

**Keyword (optional)** must appear in the response body, ignoring case; **Fail if the keyword is present instead** turns it around. With **Regular expression** ticked the keyword is a pattern in Python syntax (still case-insensitive, searched anywhere in the body), for example `(healthy|ok)\b` or `"status":\s*"ok"`. The server refuses a pattern that does not compile, and a search that takes longer than a second fails the run instead of blocking the monitor. A run's details show **Keyword regex** with the pattern and the text it matched.

### HTTP JSON query

An **HTTP(S)** target can check one value inside a JSON response, the way Uptime Kuma's JSON query does. **JSON query (optional)** takes a [JSONata](https://docs.jsonata.org/simple) expression evaluated against the response body: `status.indicator`, `items[0].state`, `components[id = "yyzkbfz2thpt"].status` (a filter picks the list entry by one of its fields), `$count(errors)`. Once the field holds an expression, two more controls appear under it:

| Control | Meaning |
| --- | --- |
| **JSON condition** | `== equals`, `!= not equal`, `< less than`, `<= at most`, `> greater than`, `>= at least` or `contains`. |
| **Expected value** | What the result is compared with. With `==` it may stay empty: the check then passes whenever the result exists and is not `false`, so an expression such as `status = "ok"` needs nothing else. |

`==` and `!=` compare the result's text (`true`, `false` and `null` as JSON writes them, numbers as numbers); the order conditions need a number on both sides; `contains` looks for the expected text anywhere in the result, ignoring case, and searches a list as its JSON text. A query that finds nothing counts as `null`, so it passes only `!=` (or `== null`). The help line under the controls repeats the rule for the chosen condition. The form refuses a condition other than `==` without an expected value and an order condition with a non-number; the server also refuses an expression JSONata cannot parse. A run's details show **JSON query** and **JSON result**: what the expression returned (or `nothing`, or the evaluation error), coloured by the outcome, with the condition in brackets. Targets saved with the former dotted **JSON check** path open with that path as the query and its comparison as the condition.

Saving a target closes the form as soon as the change is stored; the dashboard list refreshes in the background.

## Target pages

The time-range selector offers **1h**, **6h**, **24h**, **7d** and **30d**. It controls historical statistics, charts, runs and events. The latest measurement/current path always comes from the newest run. The status strip under the statistics follows the selected range too (**Status · 7d**): 48 cells, each coloured by the worst run in it, with the cell width under the heading and the date in each cell's tooltip once the range is longer than a day. The dashboard cards keep a 24-hour strip.

Statistics and charts stay together on one page: current and range statistics (the **Avg · [range]** tile lists the best and worst run beneath the mean, and the **Median · [range]** tile next to it lists p95 and p99 beneath the median; on wide screens the tiles take two rows of four), the 24-hour status strip, latency/loss charts, latency distribution and the hour-by-day heatmap. Packet probes also show jitter. Path probes show a route timeline and **Path profile**; other probes show **Latest check** details.

The data tabs sit below the charts. Switching between them keeps the charts visible above.

| Data tab | What it contains | Available for |
| --- | --- | --- |
| **Current path** | The latest run's complete hop table and a **Text report** download. The destination row is highlighted and carries a **dst** marker. | Local MTR and Globalping MTR/traceroute |
| **Path history** | One row per hop and one column per run. Choose **Loss**, **Latency** or **Jitter**; select a column to open that run. | Local MTR and Globalping MTR/traceroute |
| **Path summary · [range]** | Aggregated per-hop statistics, including ASN (with the organisation's name when a GeoIP provider knows it), address frequency, average/maximum loss, latency, standard deviation and jitter. Expand a hop to inspect alternate addresses seen at that position. Runs in which the hop answered no probe appear as **n silent** under **Seen** and count as loss on the usual address; they are not alternates. | Local MTR and Globalping MTR/traceroute |
| **Status history** | The target's up, degraded and down periods in the selected range, newest first. A bar and a line above the list give the share and the time spent in each status and the number of status changes. Each period shows how long it lasted (**so far** while it continues), when it began and ended, the alert message that began it and a link to that run. The first period is the status the range opened with. A target younger than the range is covered from its first run; a paused target's last period stays open until it is resumed and judged again. Periods come from the status events, so clearing a target's history also clears them. | Every target |
| **Runs** | Paginated run history with **All**, **Reached** or **Passed**, **Failed**, and **Route changes** where applicable. Select a run to inspect it. Each row has a checkbox (the header one selects the page); with runs ticked, **Delete N selected** asks for confirmation and removes them with their events. Changing the page, filter or range clears the selection. | Every target |
| **Events** | This target's events in the selected range. The tab displays a count when events are present. | Every target |

**Current path** is the initial data tab for path probes; other probes open **Runs**. The browser remembers the selected tab. When a remembered path tab does not apply to the next target, the page shows **Runs** instead. Only the tab on screen is refreshed: **Path history**, **Path summary** and **Runs** load when opened, so a busy page does not slow down **Save**, **Run now** and the other buttons. **Path summary** also refreshes while the **Path profile** shows **Avg · [range]**.

**Path profile**, above the data tabs, compares per-hop latency and loss. Switch between **Latest run** and **Avg · [range]**. Intermediate-hop loss alone does not establish loss at the destination.

### Path map

With ip-api.com switched on under **Settings → ip-api.com GeoIP** or a MaxMind licence key saved under **Settings → MaxMind GeoIP**, a **Path map** card sits between the latency distribution and the hour-by-day heatmap (**Location map** on ping, HTTP, TCP and DNS targets). It plots the latest completed run.

How to read it:

- **A marker is a place, not a hop.** The label on a marker says what was located there: **Monitor** (or **Probe** for Globalping), the hop numbers (**3–5**, **3, 7**), or **Target**. A label such as **Target · 7, 8** means hops 7 and 8 were placed in the target's city as well. Under the label the marker prints the IP address located there: the target's own address on the **Target** marker, the public address the monitor was placed by, and a hop's address. When a place holds several addresses the marker shows the first and how many more (**203.0.113.1 +2**); hovering the marker lists them all. Select a marker for the host names, addresses, networks, latency and loss of everything it holds.
- **The dashed line follows the hops in order** from the monitor to the target. Because GeoLite2 knows a city at best and often registers backbone routers at their operator's head office, the line can double back or reach the target's city several hops before the last hop. That is the database's estimate, not a routing fault; the marker labels say which hops are where.
- **The target is green** when the run reached it and red when it did not.
- **Other probe types show the far end too.** A ping or TCP target is the address probed; an HTTP target is the URL's host; a DNS check shows the **Resolver** it asked, or, with no resolver configured, the **Answer** address the name resolved to. Globalping checks show the remote probe and the address it resolved.
- **Route by place**, under the map, spells the same path out in words: *Berlin monitor › Frankfurt hops 3–4 › London hop 6 › Frankfurt hop 7 › London hop 8, target*. Each step prints its IP address after the hop numbers (the first address and a count such as **+1** when several hops share the step; hovering lists them all) and names the networks it crosses, as the autonomous system number and the organisation behind it (**AS64500 Example Transit GmbH**). Each step is a button: select it to pan the map to that place, open its marker popup and expand a panel listing that step's hops with host names, addresses, networks, latency and loss. On narrow screens the list becomes one step per row, the parts of a step wrap as whole pieces (place, hops, address, networks), and each row has a leading marker (a dot for the start, a chevron for every following place), so the rows line up. Select it again to close the panel. Selecting a marker on the map opens the matching step. With a keyboard, use Left/Right or Home/End to move between steps and Enter or Space to open one.
- **Networks**, under the route, lists the autonomous systems the path crosses from start to end, once each, in order. The names come from ip-api.com or the GeoLite2 ASN database, which is downloaded together with the City database. A hop keeps the number mtr reported (the **ASN lookup** setting); the provider adds the organisation name, or the number when mtr reported none. When mtr's number and the provider disagree, the number stays and no name is attached. Until a provider can name networks the line says so and links to Settings.
- **Not on the map** lists the hops without a location and why: private address, not in database, or no response.
- **Evidence.** The line **Monitor position** (or **Probe position**) under the map says how the start marker was placed (by the server's own address, by the public address it is seen from, or by the probe's own report) and the accuracy radius GeoLite2 gives for it (ip-api.com states none). Marker popups and the expanded route step show the same radius for every place. A radius of hundreds of kilometres means the marker is a regional guess. Use the **GeoIP lookup** page to check any address yourself.

Use the **+**/**−** controls or pinch to zoom; the mouse wheel keeps scrolling the page. The caption counts how many hops were located. The information icon beside the heading repeats this guidance. Without a key, the card is replaced by a one-line pointer to Settings; while the database is still downloading, the card says so.

### Hop table columns

Current path, individual run pages and quick trace show the complete hop table immediately. The original 14 columns are always available, without a column-mode selector.

| Column | Meaning |
| --- | --- |
| **#** | Hop number |
| **Host** | Full hostname and IP address, or the IP when no hostname is available |
| **ASN** | Autonomous system number, when available, with the organisation's name beneath it when a GeoIP provider (ip-api.com or the GeoLite2 ASN database) knows it |
| **Loss** | Packet loss percentage |
| **Snt / Rcv** | Packets sent and received |
| **Last / Avg / Best / Wrst** | Last, average, best and worst round-trip latency |
| **StDev** | Latency standard deviation |
| **Jitter / Jmax** | Average and maximum jitter |
| **Latency** | Best-to-worst latency bar with an average marker |

Timing columns use milliseconds and loss uses percent. Tables show their full height; scroll horizontally to reach additional columns on narrow screens. Hostnames and IP addresses are not truncated. “No response” means a hop did not report an address. A dash means the requested measurement is unavailable. A previously saved compact-view preference does not hide any columns.

## GeoIP lookup

**GeoIP lookup** in the menu answers where the configured GeoIP provider (ip-api.com first, then the GeoLite2 database) places one address. Enter an IP address or a host name (host names are resolved first) and select **Look up**, or select **This server** to locate the monitor exactly as the path map does. The **Provider** switch chooses the backend: **Automatic** asks exactly as the map does (ip-api.com first when it is switched on, then the GeoLite2 databases), **ip-api.com** and **MaxMind GeoLite2** ask that backend alone so their answers can be compared; ip-api.com can be asked here even while its switch is off, within the same request budget. Changing the provider with a result on screen repeats the lookup at once, and the result lists the provider used. The result lists what was looked up, the kind (address, host name, or how this server was placed), the resolved address, the place, the coordinates, the accuracy radius, the **Network** (autonomous system number and organisation from ip-api.com or the GeoLite2 ASN database) and the **Source** (which provider answered, and the database build date), and shows the point on a map. A private address, an address missing from the provider or an unresolvable host name is reported in words. Without any provider set up, before the databases have been downloaded, or while ip-api.com is paused after a failure, the page says so and points to Settings.

## Charts, scales and help

Latency and jitter read in milliseconds below one second and in seconds from 1000 ms up ("1.23 s"), on cards, tiles, tables, tooltips, chart axes and heatmap legends; in a column headed "(ms)" such a value carries its own "s". Values printed side by side (best / worst, p95 · p99) share one unit while all are below a second; otherwise each names its own.

The latency chart's line represents average latency and its band spans best to worst. Dashed markers identify route changes across the whole range. A change whose route already appeared among the recent runs (the **Chart route memory** setting, default 20) gets no marker, so a load-balanced path flapping between a few routes stays readable while a route not seen in that window is still marked; the events and counts elsewhere list every change. When the memory hides markers, a line under the chart caption says how many ("5 route changes hidden by the chart memory (20 runs)") and its **Chart route memory** link opens Settings at that field. When markers are denser than a few pixels apart they are thinned evenly rather than cut off, so old changes stay visible. Select a point to open its associated run. The **Route in use** strip under the chart colours each stretch of time by the path in use; a hop that answered nothing in one run is treated as a wildcard, so rate-limited routers do not create extra routes or split a segment. Hover a route's label to see whether such runs were folded into it. Long ranges may use bucketed averages; read the caption to see the aggregation interval. The latency histogram uses those same series points, so a long-range histogram can describe bucket averages rather than individual runs.

Both heatmaps show numeric legends:

- **Loss** uses fixed percentage stops at 0, 2, 10, 40 and 100.
- **Latency** and **Jitter** use a sequential colour scale from zero to the maximum in the displayed data, labelled in milliseconds. Check the numeric scale when comparing different targets or ranges.
- In latency/jitter views, red identifies no response. In the hourly heatmap, a red cell means the hour contains runs but no successful responses. Neutral cells indicate no data; in path history this also includes a hop position absent from a run.

The **Hour by day** grid uses the browser's local time. Chart timestamps also use local time. Hover tooltips provide details where supported; on a phone, use the visible legends and run detail pages for precise values.

Select an information icon beside a chart heading to open its explanation. Help works by click, tap or keyboard, and closes with **Close help**, **Escape**, or a click/tap outside it.

## Phones, themes and keyboard controls

On narrow screens, open **Menu** in the sticky header for **Dashboard**, **Events**, **Quick trace**, **GeoIP lookup** and **Settings**. The **Switch theme** button cycles **Dark → OLED → Light**. On desktop, choose a theme at the bottom of the sidebar. The choice is remembered in this browser.

Target tabs and wide data tables scroll horizontally when needed. The three-dot action menu opens over the page so it remains visible inside a scrolling table.

With a keyboard, use Left/Right or Home/End in the data tabs and in the map's **Route by place** steps. In an action menu, use Up/Down or Home/End to select an item, Enter to activate it, and Escape to close it and return focus to the menu button. A **New group name** field (target page header, dashboard selection bar) saves with Enter and cancels with Escape.

## Settings and saved preferences

The **Pushover** card has **End-to-end encryption key (optional)** at the bottom, with **Show**/**Hide**, **Copy** (enabled once a valid key is entered) and **Generate key** (a new random 256-bit key, shown so it can be copied into the Pushover app). The line under it says whether encryption is **On** or **Off**, or that the key must be exactly 64 hexadecimal characters. On a phone the key field takes its own line above the buttons. With a key saved, pushes reach only devices holding that key; **Send test** uses the key in the form, so it checks the setup before saving.

**Settings** has a **Notification cooldown** card above **Webhook** with one field, **Cooldown (minutes)** (0 to 1440 minutes, that is up to one day; 0 sends every alert). With a cooldown set, each target sends at most one alert of a kind (status, or route change) per period; a problem getting worse, such as degraded turning into down, is sent at once, and when the period ends a catch-up alert reports the current status if it changed since the last alert, naming how many alerts were held back. The help line under the field restates the rule for the value entered. In the event lists, an event whose alert waited shows **notification held back by the cooldown** next to its time (hover for the explanation).

**Settings** controls retention, the chart route memory (the latency chart drops a route-change marker when the run's route appeared among that many recent reached runs; events are unaffected; 1 marks every stored change; the link under a latency chart's caption lands on this field), name/ASN lookups, notification channels, public URL, Globalping credentials, the ip-api.com switch, MaxMind GeoIP credentials and tag colours. The **ip-api.com GeoIP** section has one **Enabled** switch and shows whether the provider is ready or paused (with the reason), how many of the allowed batch requests were used this minute, how many addresses are cached, the last answer and the last failed request. The **MaxMind GeoIP** section shows whether the GeoLite2 City database (**Locations**) and the GeoLite2 ASN database (**Networks**) are present, their build dates and the last download error, and offers **Download now** once a licence key is saved; the button fetches both. It also offers **Export JSON** and **Import JSON** for target definitions; these exports do not contain monitoring history (the history of an installation that used SQLite is carried over by the automatic import at start, not by this file). Import modes update by name, always create, or replace all targets. The **Engine** card lists **Version**, **Uptime**, **mtr** (the binary's version, or that it was not found), **Mode** (live probes or simulation), **Concurrency**, **Active now**, **Runs since start**, **Runs stored**, **Database size** (the PostgreSQL database on disk, the server's own catalogs included; it does not shrink after a purge) and, when the API token is saved here or none is required, **Database** (the connection URL without its password). The **Environment variables** card lists the variables read at startup with the values the running server applied, defaults included: the API token reads only "(set)" or nothing, and `MTR_TRACKER_DATABASE_URL` (without its password) and `MTR_TRACKER_DATA_DIR` read "hidden without the API token" unless the token is saved here or none is required. Long values wrap inside the card.

Theme, dashboard view/sort, comparison-chart visibility, data tab and time range are saved in browser storage. They are preferences for that browser, not global settings.

If the server requires an API token, enter it under **Settings → API access** or in the prompt after an unauthorized write. Save the token, then retry the action. The token is stored in this browser; reading the dashboard does not require it.
