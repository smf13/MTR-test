import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type Hop, type HopSummaryEntry, type Target, type RunDetail, type ProbeType, type ProbeOptions } from "../src/api";
import { TargetActions } from "../src/components/TargetActions";
import { HelpTip } from "../src/components/Popover";
import { HopTable } from "../src/components/HopTable";
import { thinRouteChanges } from "../src/components/Charts";
import { Sparkline } from "../src/components/Sparkline";
import { HeatLegend } from "../src/components/HopHeatmap";
import { TargetDetail } from "../src/pages/TargetDetail";
import { Dashboard } from "../src/pages/Dashboard";

const timestamp = "2026-09-17T12:00:00Z";
const hop: Hop = {
  hop_no: 1, ip: "192.0.2.1", hostname: "router.example", asn: "AS64500", as_name: "Example Transit GmbH",
  loss_pct: 0, sent: 10, received: 10, last_ms: 12, avg_ms: 12, best_ms: 10,
  worst_ms: 15, stdev_ms: 2, gmean_ms: 12, jitter_ms: 2, jitter_avg_ms: 2,
  jitter_max_ms: 3, jitter_int_ms: 2,
};
const run: RunDetail = {
  id: 10, target_id: 1, target_name: "Office WAN", target_host: "192.0.2.1", target_type: "mtr",
  started_at: timestamp, finished_at: timestamp, duration_ms: 1000, status: "ok", error: null,
  src: "simulation", dst_ip: "192.0.2.1", reached: true, hop_count: 1, sent: 10,
  loss_pct: 0, last_ms: 12, avg_ms: 12, best_ms: 10, worst_ms: 15, stdev_ms: 2,
  jitter_avg_ms: 2, jitter_max_ms: 3, route_hash: "example", route_changed: false,
  command: null, details: null, hops: [hop], prev_run_id: null, next_run_id: null,
};
const target: Target = {
  id: 1, name: "Office WAN", host: "192.0.2.1", type: "mtr", options: {}, description: "",
  tags: ["wan"], group_name: "", interval_sec: 60, count: 10, probe_interval: 1, protocol: "icmp", port: null,
  packet_size: 64, ip_version: "auto", max_hops: 30, enabled: true, notify: true, alert_loss_pct: 5,
  alert_latency_ms: 200, created_at: timestamp, updated_at: timestamp, next_run_at: null,
  last_status: "up", latest_run: run, stats_24h: { runs: 1, availability_pct: 100, avg_ms: 12, median_ms: 11, loss_pct: 0, route_changes: 0 },
  sparkline: [{ t: timestamp, avg: 12, loss: 0, reached: true }],
  timeline: { bucket_sec: 1800, since: timestamp, buckets: [{ s: "up", n: 1, avg: 12 }] },
};

function mockDetail(t: Target = target) {
  vi.spyOn(api, "target").mockResolvedValue(t);
  vi.spyOn(api, "series").mockResolvedValue({ points: [], bucket_sec: null, range_sec: 86400, route_changes: { stored: 0, marked: 0, hidden: 0, memory: 20 } });
  vi.spyOn(api, "runs").mockResolvedValue({ total: 1, items: [run] });
  vi.spyOn(api, "run").mockResolvedValue(run);
  vi.spyOn(api, "hopHistory").mockResolvedValue({ runs: [], max_hops: 0 });
  vi.spyOn(api, "hopSummary").mockResolvedValue({ total_runs: 0, hops: [] });
  vi.spyOn(api, "targetEvents").mockResolvedValue([]);
  vi.spyOn(api, "statusHistory").mockResolvedValue({
    range_sec: 86400, since: "2026-09-16T12:00:00Z", start: "2026-09-16T12:00:00Z", paused: false, changes: 2, truncated: false, uptime_pct: 95.833,
    totals: { up: 82800, degraded: 0, down: 3600 },
    periods: [
      { status: "up", start: "2026-09-17T10:00:00Z", end: null, ongoing: true, duration_sec: 7200, message: "Office WAN recovered (down -> up)", run_id: 12 },
      { status: "down", start: "2026-09-17T09:00:00Z", end: "2026-09-17T10:00:00Z", ongoing: false, duration_sec: 3600, message: "Office WAN is DOWN: destination unreachable", run_id: 11 },
      { status: "up", start: "2026-09-16T12:00:00Z", end: "2026-09-17T09:00:00Z", ongoing: false, duration_sec: 75600, message: null, run_id: null },
    ],
  });
  vi.spyOn(api, "routes").mockResolvedValue({ routes: [], segments: [], since: timestamp, range_sec: 86400, total_runs: 0 });
  vi.spyOn(api, "hourly").mockResolvedValue({ hours: [], range_sec: 86400 });
  vi.spyOn(api, "geo").mockResolvedValue({ enabled: false, ip_api_enabled: false, available: false, asn_available: false, run_id: null, sources: [], hops: [], destination: null });
}
function renderDetail() {
  return render(<MemoryRouter initialEntries={["/targets/1"]}><Routes><Route path="/targets/:id" element={<TargetDetail />} /><Route path="/runs/:id" element={<div>Run inspection opened</div>} /></Routes></MemoryRouter>);
}

describe("target actions", () => {
  it("supports keyboard navigation, invokes the intended action, and restores focus on Escape", async () => {
    const user = userEvent.setup();
    const clone = vi.fn();
    const remove = vi.fn();
    const mute = vi.fn();
    render(<TargetActions name="Office WAN" enabled notify onToggle={vi.fn()} onToggleNotify={mute} onEdit={vi.fn()} onClone={clone} onDelete={remove} />);
    const trigger = screen.getByRole("button", { name: "Actions for Office WAN" });
    await user.click(trigger);
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Pause" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Pause", "Edit", "Clone", "Mute notifications", "Delete"]);
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(clone).toHaveBeenCalledOnce();
    expect(remove).not.toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
    await user.click(trigger);
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Delete" }));
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(trigger);
    expect(screen.queryByRole("menu")).toBeNull();
    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: "Mute notifications" }));
    expect(mute).toHaveBeenCalledOnce();
  });

  it("labels the menu and the card for a muted target", async () => {
    const user = userEvent.setup();
    const muted = { ...target, notify: false };
    vi.spyOn(api, "targets").mockResolvedValue([muted]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    const update = vi.spyOn(api, "updateTarget").mockResolvedValue({ ...muted, notify: true });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    const card = await screen.findByRole("article", { name: "Office WAN" });
    expect(within(card).getByText("Muted")).toBeTruthy();
    await user.click(within(card).getByRole("button", { name: "Actions for Office WAN" }));
    await user.click(screen.getByRole("menuitem", { name: "Unmute notifications" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { notify: true }));
  });

  it("dismisses help with Escape and outside clicks", async () => {
    const user = userEvent.setup();
    render(<><HelpTip label="latency">Measured in milliseconds.</HelpTip><button>Outside</button></>);
    const trigger = screen.getByRole("button", { name: "About latency" });
    await user.click(trigger);
    expect(screen.getByRole("dialog").textContent).toContain("milliseconds");
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(trigger);
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("route-change markers", () => {
  it("keeps markers across the whole range, thinned by pixel distance rather than cut off by count", () => {
    // 288 runs over 24 hours, every one a route change (a load-balanced path), on a 600px plot.
    const day = 86_400_000;
    const start = 1_700_000_000_000;
    const rows = Array.from({ length: 288 }, (_, i) => ({ t: start + i * 300_000, routeChanged: true }));
    const kept = thinRouteChanges(rows, [start, start + day], 600, 3);
    // 600px / 3px = 200 slots at most, and the markers reach from the first run to the last.
    expect(kept.length).toBeGreaterThan(100);
    expect(kept.length).toBeLessThanOrEqual(200);
    expect(kept[0].t).toBe(start);
    expect(kept[kept.length - 1].t).toBeGreaterThanOrEqual(start + day - 600_000);
    // Sparse changes are all kept, in order, and unflagged runs never appear.
    const sparse = rows.map((r, i) => ({ ...r, routeChanged: i % 50 === 0 }));
    expect(thinRouteChanges(sparse, [start, start + day], 600).map((r) => r.t)).toEqual(sparse.filter((r) => r.routeChanged).map((r) => r.t));
    // A zero-width plot (before the ResizeObserver reports) still returns a marker rather than throwing.
    expect(thinRouteChanges(rows, [start, start + day], 0).length).toBeGreaterThanOrEqual(1);
  });
});

describe("range statistics", () => {
  it("shows the median as its own tile next to the average", async () => {
    mockDetail({ ...target, stats: { range_sec: 86400, runs: 10, ok_runs: 10, failed_runs: 0, availability_pct: 100, avg_ms: 18.4, p50_ms: 12.1, p95_ms: 40, p99_ms: 55, best_ms: 9, worst_ms: 70, loss_pct: 0, max_loss_pct: 0, jitter_ms: 2, route_changes: 0, hop_count_min: 1, hop_count_max: 1, events: 0 } });
    renderDetail();
    const median = await screen.findByText("Median · 24h");
    expect(median.parentElement?.parentElement?.textContent).toContain("12.1 ms");
    expect(median.parentElement?.parentElement?.textContent).toContain("p95 40.0 · p99 55.0");
    const avg = median.parentElement?.parentElement?.previousElementSibling;
    expect(avg?.textContent).toContain("Avg · 24h");
    expect(avg?.textContent).toContain("18.4 ms");
    expect(avg?.textContent).toContain("best 9.0 · worst 70.0");
  });
});

describe("chart route memory note", () => {
  it("says how many route changes the memory hid and links to the setting", async () => {
    mockDetail();
    vi.spyOn(api, "series").mockResolvedValue({ points: [], bucket_sec: null, range_sec: 86400, route_changes: { stored: 7, marked: 2, hidden: 5, memory: 20 } });
    renderDetail();
    const note = await screen.findByTestId("route-memory-note");
    expect(note.textContent).toBe("5 route changes hidden by the chart memory (20 runs) · Chart route memory");
    expect(within(note).getByRole("link", { name: "Chart route memory" }).getAttribute("href")).toBe("/settings#route-memory");
  });

  it("stays silent when nothing is hidden", async () => {
    mockDetail();
    renderDetail();
    await screen.findByText("Round-trip time to destination");
    expect(screen.queryByTestId("route-memory-note")).toBeNull();
  });
});

describe("hop inspection", () => {
  it("shows every original metric immediately even with a saved compact preference", () => {
    localStorage.setItem("mtr-tracker.hop-columns", JSON.stringify("compact"));
    const detailedHop = { ...hop, hostname: "long-router-name.branch-office.network.example", loss_pct: 5, sent: 20, received: 19, last_ms: 13, jitter_avg_ms: 2.5 };
    render(<HopTable hops={[detailedHop]} dstIp={hop.ip} />);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(["#", "Host", "ASN", "Loss", "Snt", "Rcv", "Last", "Avg", "Best", "Wrst", "StDev", "Jitter", "Jmax", "Latency"]);
    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
    expect(within(cells[1]).getByText(detailedHop.hostname)).toBeTruthy();
    expect(within(cells[1]).getByText(hop.ip!)).toBeTruthy();
    // The ASN cell carries the organisation's name under the number.
    expect(within(cells[2]).getByText("AS64500")).toBeTruthy();
    expect(within(cells[2]).getByTitle("Example Transit GmbH").textContent).toBe("Example Transit GmbH");
    expect(cells.slice(3, 13).map((cell) => cell.textContent)).toEqual(["5.0%", "20", "19", "13.0", "12.0", "10.0", "15.0", "2.0", "2.5", "3.0"]);
    render(<HopTable hops={[{ ...detailedHop, hop_no: 2, as_name: null }]} />);
    expect(within(screen.getAllByRole("row")[3]).getAllByRole("cell")[2].textContent).toBe("AS64500");
    expect(within(cells[13]).getByTitle("best 10.0 · avg 12.0 · worst 15.0 ms")).toBeTruthy();
    expect(screen.getByText("dst")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "All metrics" })).toBeNull();
  });

  it("shows a numerical scale in the metric's units", () => {
    render(<HeatLegend metric="jitter" max={80} />);
    const legend = screen.getByLabelText("Jitter scale");
    expect(legend.textContent).toContain("Jitter (ms)");
    expect(within(legend).getByText("20.0")).toBeTruthy();
    expect(within(legend).getByText("80.0")).toBeTruthy();
    expect(within(legend).getByText("No response")).toBeTruthy();
  });

  it("keeps sparklines fluid and does not fill across a missing run", () => {
    const points = [12, 14, null, 20, 22].map((avg) => ({ t: timestamp, avg, loss: avg === null ? 100 : 0, reached: avg !== null }));
    const { container } = render(<><Sparkline points={points} fluid /><Sparkline points={points} fluid /></>);
    const svgs = container.querySelectorAll("svg");
    expect(svgs[0].getAttribute("width")).toBe("100%");
    expect(container.querySelectorAll("linearGradient")[0].id).not.toBe(container.querySelectorAll("linearGradient")[1].id);
    const fill = svgs[0].querySelector("path[fill]")?.getAttribute("d") ?? "";
    expect(fill.match(/Z/g)).toHaveLength(2);
  });
});

describe("detail navigation", () => {
  it("restores the original data tabs below the charts with the full current path open", async () => {
    const user = userEvent.setup();
    localStorage.setItem("mtr-tracker.detail-view", JSON.stringify("overview"));
    localStorage.setItem("mtr-tracker.hop-columns", JSON.stringify("compact"));
    mockDetail();
    renderDetail();
    const navigation = await screen.findByRole("tablist", { name: "Target data" });
    expect(within(navigation).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Current path", "Path history", "Path summary · 24h", "Status history", "Runs", "Events"]);
    const currentPath = within(navigation).getByRole("tab", { name: "Current path" });
    expect(currentPath.getAttribute("aria-selected")).toBe("true");
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(14);
    const profile = screen.getByRole("heading", { name: /Path profile/ });
    expect(profile.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Hour by day/ }).compareDocumentPosition(navigation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(within(navigation).getByRole("tab", { name: "Path history" }));
    expect(screen.getByText("One column per run")).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    expect(within(navigation).getByRole("tab", { name: "Path summary · 24h" }).getAttribute("aria-selected")).toBe("true");
    await user.click(within(navigation).getByRole("tab", { name: "Runs" }));
    // The tab lights up on the tap; the panel behind it renders as a transition a moment later.
    await waitFor(() => expect(screen.getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(["", "Started", "Result", "Hops", "Loss", "Avg", "Best", "Wrst", "StDev", "Jitter", "Duration", ""]));
    expect(screen.getByRole("button", { name: "Route changes" })).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    expect(within(navigation).getByRole("tab", { name: "Events" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("heading", { name: /Path profile/ })).toBe(profile);
    expect(screen.getByRole("heading", { name: /Latency distribution/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Round-trip time to destination" })).toBeTruthy();
    await user.keyboard("{Home}");
    expect(currentPath.getAttribute("aria-selected")).toBe("true");
    await user.keyboard("{End}{ArrowLeft}");
    expect(within(navigation).getByRole("tab", { name: "Runs" }).getAttribute("aria-selected")).toBe("true");
    expect(JSON.parse(localStorage.getItem("mtr-tracker.tab")!)).toBe("runs");
  });

  it("lists the status history of the selected range, newest period first", async () => {
    const user = userEvent.setup();
    mockDetail();
    renderDetail();
    const navigation = await screen.findByRole("tablist", { name: "Target data" });
    await user.click(within(navigation).getByRole("tab", { name: "Status history" }));
    await waitFor(() => expect(screen.getAllByTestId("status-period")).toHaveLength(3));
    expect(vi.mocked(api.statusHistory)).toHaveBeenCalledWith(1, "24h");
    const [current, outage, first] = screen.getAllByTestId("status-period");
    expect(current.textContent).toContain("2h so far");
    expect(current.textContent).toContain("now");
    expect(outage.textContent).toContain("Office WAN is DOWN: destination unreachable");
    expect(within(outage).getByRole("link", { name: "Run #11" }).getAttribute("href")).toBe("/runs/11");
    expect(first.textContent).toContain("Status when the range begins");
    expect(screen.getByTestId("status-totals").textContent).toContain("Down4.17% · 1h");
    expect(screen.getByTestId("status-totals").textContent).toContain("2 status changes · 24h");
  });

  it("restores a remembered path summary with all metrics and expandable alternate addresses", async () => {
    const user = userEvent.setup();
    localStorage.setItem("mtr-tracker.tab", JSON.stringify("summary"));
    mockDetail();
    const primary: HopSummaryEntry = { ip: hop.ip, hostname: hop.hostname, asn: hop.asn, as_name: hop.as_name, runs: 8, share_pct: 80, loss_pct: 1, max_loss_pct: 5, avg_ms: 12, best_ms: 10, worst_ms: 15, stdev_ms: 2, jitter_ms: 3, jitter_max_ms: 4 };
    vi.mocked(api.hopSummary).mockResolvedValue({ total_runs: 10, hops: [{ hop: 1, primary, alternates: [{ ...primary, ip: "192.0.2.2", hostname: "alternate.example", asn: "AS64501", as_name: null, runs: 2, share_pct: 20 }], silent_runs: 3 }] });
    renderDetail();
    const table = await screen.findByRole("table");
    expect(screen.getByRole("tab", { name: "Path summary · 24h" }).getAttribute("aria-selected")).toBe("true");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(["#", "Host", "ASN", "Seen", "Loss", "Max loss", "Avg", "Best", "Wrst", "StDev", "Jitter", "Latency"]);
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    // Runs where the hop answered nothing are shown as silence on the primary row, never as an extra address.
    expect(within(within(table).getAllByRole("row")[1]).getByText("3 silent")).toBeTruthy();
    // The ASN cell names the organisation under the number, as the hop table does.
    expect(within(within(table).getAllByRole("row")[1]).getAllByRole("cell")[2].textContent).toBe("AS64500Example Transit GmbH");
    const toggle = screen.getByRole("button", { name: "1 alternate address seen at this hop" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[2]).getByText("alternate.example")).toBeTruthy();
    expect(within(rows[2]).getByText("192.0.2.2")).toBeTruthy();
    expect(within(rows[2]).getAllByRole("cell").slice(2, 11).map((cell) => cell.textContent)).toEqual(["AS64501", "20%", "1.0%", "5.0%", "12.0", "10.0", "15.0", "2.0", "3.0"]);
    expect(screen.getByRole("heading", { name: /Path profile/ })).toBeTruthy();
  });

  it("keeps run pagination, filtering and opening a run available in the restored data tabs", async () => {
    const user = userEvent.setup();
    localStorage.setItem("mtr-tracker.tab", JSON.stringify("runs"));
    mockDetail();
    vi.mocked(api.runs).mockImplementation(async (_id, options) => options?.limit === 1
      ? { total: 1, items: [run] }
      : { total: 26, items: [{ ...run, id: options?.offset ? 11 : 10, route_changed: options?.status === "route_change" }] });
    renderDetail();
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(api.runs).toHaveBeenCalledWith(1, { limit: 25, offset: 25, range: "24h", status: undefined }));
    expect(await screen.findByText("26–26 of 26")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Route changes" }));
    await waitFor(() => expect(api.runs).toHaveBeenCalledWith(1, { limit: 25, offset: 0, range: "24h", status: "route_change" }));
    expect(await screen.findByText("1–25 of 26")).toBeTruthy();
    await user.click(await within(screen.getByRole("table")).findByText("route change"));
    expect(await screen.findByText("Run inspection opened")).toBeTruthy();
  });

  it.each<[ProbeType, ProbeOptions, boolean]>([
    ["mtr", {}, true], ["ping", {}, false], ["http", {}, false], ["tcp", {}, false], ["dns", {}, false],
    ["globalping", { measurement: "mtr" }, true], ["globalping", { measurement: "traceroute" }, true],
    ["globalping", { measurement: "http" }, false],
  ])("preserves applicable views for %s %j", async (type, options, path) => {
    localStorage.setItem("mtr-tracker.tab", JSON.stringify("path"));
    mockDetail({ ...target, type, options, latest_run: null });
    renderDetail();
    const navigation = await screen.findByRole("tablist", { name: "Target data" });
    expect(!!within(navigation).queryByRole("tab", { name: "Current path" })).toBe(path);
    expect(within(navigation).getByRole("tab", { name: path ? "Current path" : "Runs" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("heading", { name: path ? /Path profile/ : /Latest check/ })).toBeTruthy();
  });
});

describe("dashboard", () => {
  it("shows mean and median latency across the targets' latest runs", async () => {
    const withLatency = (id: number, avg_ms: number | null): Target => ({
      ...target, id, name: `Target ${id}`, latest_run: avg_ms === null ? null : { ...run, id: id * 10, target_id: id, avg_ms },
    });
    vi.spyOn(api, "targets").mockResolvedValue([withLatency(1, 12), withLatency(2, 20), withLatency(3, 100), withLatency(4, null)]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByRole("article", { name: "Target 1" });
    expect(screen.getByText("Mean latency").parentElement?.parentElement?.textContent).toContain("44.0 ms");
    expect(screen.getByText("Median latency").parentElement?.parentElement?.textContent).toContain("20.0 ms");
  });

  it("shows each card's 24-hour average and median latency", async () => {
    vi.spyOn(api, "targets").mockResolvedValue([{ ...target, stats_24h: { ...target.stats_24h, avg_ms: 18.4, median_ms: 12.1 } }]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    const card = await screen.findByRole("article", { name: "Office WAN" });
    expect(within(card).getByTestId("card-latency-24h").textContent).toBe("24h avg 18.4 ms24h median 12.1 ms");
  });

  it("puts grouped targets in collapsible sections that remember their state", async () => {
    const user = userEvent.setup();
    const member = (id: number, name: string, group_name: string, last_status: Target["last_status"] = "up"): Target => ({ ...target, id, name, group_name, last_status });
    vi.spyOn(api, "targets").mockResolvedValue([member(1, "Branch A", "branches"), member(2, "Branch B", "branches", "down"), member(3, "Core", "Datacentre"), member(4, "Loose", "")]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByRole("article", { name: "Branch A" });
    const headers = screen.getAllByRole("button", { expanded: true }).filter((b) => b.classList.contains("group-header"));
    // Case-insensitive order, ungrouped targets last.
    expect(headers.map((h) => h.textContent)).toEqual([expect.stringMatching(/^branches2/), expect.stringMatching(/^Datacentre1/), expect.stringMatching(/^Ungrouped1/)]);
    expect(within(headers[0]).getByTestId("group-summary").textContent).toBe("1 down1 up");
    await user.click(headers[0]);
    expect(headers[0].getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("article", { name: "Branch A" })).toBeNull();
    expect(screen.getByRole("article", { name: "Core" })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("mtr-tracker.collapsed-groups")!)).toEqual(["branches"]);
    // A filter shows matches inside collapsed groups, and matches the group name too.
    await user.type(screen.getByRole("textbox", { name: "Filter targets" }), "branch");
    expect(screen.getByRole("article", { name: "Branch B" })).toBeTruthy();
    expect(screen.queryByRole("article", { name: "Core" })).toBeNull();
    await user.clear(screen.getByRole("textbox", { name: "Filter targets" }));
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryAllByRole("article")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getAllByRole("article")).toHaveLength(4);
  });

  it("groups the table view under the same headings", async () => {
    const user = userEvent.setup();
    localStorage.setItem("mtr-tracker.view", JSON.stringify("table"));
    vi.spyOn(api, "targets").mockResolvedValue([{ ...target, id: 1, name: "Branch A", group_name: "Branches" }, { ...target, id: 2, name: "Loose", group_name: "" }]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByRole("link", { name: "Branch A" });
    await user.click(screen.getByRole("button", { name: /^Branches/ }));
    expect(screen.queryByRole("link", { name: "Branch A" })).toBeNull();
    expect(screen.getByRole("link", { name: "Loose" })).toBeTruthy();
  });

  it("shows the plain list while no target has a group", async () => {
    vi.spyOn(api, "targets").mockResolvedValue([target]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByRole("article", { name: "Office WAN" });
    expect(screen.queryByRole("button", { name: "Collapse all" })).toBeNull();
    expect(document.querySelector(".group-header")).toBeNull();
  });

  it("moves selected targets into an existing or a new group", async () => {
    const user = userEvent.setup();
    const member = (id: number, name: string, group_name: string): Target => ({ ...target, id, name, group_name });
    vi.spyOn(api, "targets").mockResolvedValue([member(1, "Branch A", "Branches"), member(2, "Core", "Datacentre"), member(3, "Loose", ""), member(4, "Spare", "")]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    const move = vi.spyOn(api, "moveToGroup").mockResolvedValue({ action: "group", affected: [] });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByRole("article", { name: "Loose" });
    expect(screen.queryByRole("checkbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Select" }));
    const bar = screen.getByRole("region", { name: "Selected targets" });
    const menu = within(bar).getByRole("combobox", { name: "Move selected targets to group" }) as HTMLSelectElement;
    expect(menu.disabled).toBe(true);
    await user.click(screen.getByRole("checkbox", { name: "Select Loose" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Spare" }));
    expect(within(bar).getByTestId("selection-count").textContent).toBe("2 selected");
    // The Ungrouped heading's box reflects that both of its targets are chosen.
    expect((screen.getByRole("checkbox", { name: "Select every target in Ungrouped" }) as HTMLInputElement).checked).toBe(true);
    expect([...menu.options].map((o) => o.textContent)).toEqual(["Move to group…", "Branches", "Datacentre", "Remove from group", "New group…"]);
    await user.selectOptions(menu, "Branches");
    expect(move).toHaveBeenCalledWith([3, 4], "Branches");
    await waitFor(() => expect(within(bar).getByTestId("selection-count").textContent).toBe("0 selected"));

    // A whole group at once, into a group that does not exist yet.
    await user.click(screen.getByRole("checkbox", { name: "Select every target in Datacentre" }));
    await user.selectOptions(within(bar).getByRole("combobox", { name: "Move selected targets to group" }), "New group…");
    await user.type(within(bar).getByRole("textbox", { name: "New group name" }), "  Core   sites ");
    await user.click(within(bar).getByRole("button", { name: "Save" }));
    expect(move).toHaveBeenLastCalledWith([2], "Core sites");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("selects targets from the table view too", async () => {
    const user = userEvent.setup();
    localStorage.setItem("mtr-tracker.view", JSON.stringify("table"));
    vi.spyOn(api, "targets").mockResolvedValue([{ ...target, id: 1, name: "Branch A", group_name: "Branches" }, { ...target, id: 2, name: "Loose", group_name: "" }]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    const move = vi.spyOn(api, "moveToGroup").mockResolvedValue({ action: "group", affected: [] });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByRole("link", { name: "Branch A" });
    await user.click(screen.getByRole("button", { name: "Select" }));
    await user.click(screen.getByRole("checkbox", { name: "Select every target shown" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Move selected targets to group" }), "Remove from group");
    expect(move).toHaveBeenCalledWith([1, 2], "");
  });

  it("renames a group, warns before merging and keeps it collapsed", async () => {
    const user = userEvent.setup();
    localStorage.setItem("mtr-tracker.collapsed-groups", JSON.stringify(["branches"]));
    vi.spyOn(api, "targets").mockResolvedValue([{ ...target, id: 1, name: "Branch A", group_name: "branches" }, { ...target, id: 2, name: "Core", group_name: "Datacentre" }]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    const rename = vi.spyOn(api, "renameGroup").mockResolvedValue({ name: "branches", new_name: "Branch offices", renamed: 1, merged: false });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByRole("article", { name: "Core" });
    expect(screen.queryByRole("button", { name: /Rename group Ungrouped/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Rename group branches" }));
    const input = screen.getByRole("textbox", { name: "New name" });
    await user.clear(input);
    await user.type(input, "Datacentre");
    expect(screen.getByTestId("rename-group-help").textContent).toMatch(/already exists/);
    expect(screen.getByRole("button", { name: "Merge groups" })).toBeTruthy();
    await user.clear(input);
    await user.type(input, "Branch  offices{Enter}");
    expect(rename).toHaveBeenCalledWith("branches", "Branch offices");
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "New name" })).toBeNull());
    expect(JSON.parse(localStorage.getItem("mtr-tracker.collapsed-groups")!)).toEqual(["Branch offices"]);
  });

  it("keeps clone prefill and filtering working with the new card controls", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "targets").mockResolvedValue([target]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    const card = await screen.findByRole("article", { name: "Office WAN" });
    await user.click(within(card).getByRole("button", { name: "Actions for Office WAN" }));
    await user.click(screen.getByRole("menuitem", { name: "Clone" }));
    expect(screen.getByRole("heading", { name: "Clone Office WAN" })).toBeTruthy();
    expect(screen.getByDisplayValue("Office WAN (copy)")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.type(screen.getByRole("textbox", { name: "Filter targets" }), "missing-target");
    expect(screen.getByText("No matching targets")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Clear filter" }));
    expect(screen.getByRole("article", { name: "Office WAN" })).toBeTruthy();
  });
});

describe("target groups on the target page", () => {
  it("moves the target from the header's Group dropdown", async () => {
    const user = userEvent.setup();
    mockDetail({ ...target, group_name: "Branches" });
    vi.spyOn(api, "groups").mockResolvedValue([{ name: "Branches", count: 2 }, { name: "Datacentre", count: 1 }]);
    const update = vi.spyOn(api, "updateTarget").mockResolvedValue({ ...target, group_name: "Datacentre" });
    renderDetail();
    const menu = (await screen.findByRole("combobox", { name: "Group" })) as HTMLSelectElement;
    await waitFor(() => expect([...menu.options].map((o) => o.textContent)).toEqual(["No group", "Branches", "Datacentre", "New group…"]));
    expect(menu.value).toBe("Branches");
    await user.selectOptions(menu, "Datacentre");
    expect(update).toHaveBeenCalledWith(1, { group_name: "Datacentre" });
    expect(menu.value).toBe("Datacentre");
    await user.selectOptions(menu, "New group…");
    await user.type(screen.getByRole("textbox", { name: "New group name" }), "Labs{Enter}");
    expect(update).toHaveBeenLastCalledWith(1, { group_name: "Labs" });
    // Escape leaves the name field without a write.
    await user.selectOptions(screen.getByRole("combobox", { name: "Group" }), "New group…");
    await user.type(screen.getByRole("textbox", { name: "New group name" }), "Nope{Escape}");
    expect(update).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("combobox", { name: "Group" })).toBeTruthy();
  });

  it("offers the groups in use as a dropdown with a New group option in the form", async () => {
    const user = userEvent.setup();
    mockDetail({ ...target, group_name: "Branches" });
    vi.spyOn(api, "groups").mockResolvedValue([{ name: "Branches", count: 2 }, { name: "Datacentre", count: 1 }]);
    const update = vi.spyOn(api, "updateTarget").mockResolvedValue(target);
    renderDetail();
    await user.click(await screen.findByRole("button", { name: "Actions for Office WAN" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    const field = screen.getByRole("combobox", { name: "Group (optional)" }) as HTMLSelectElement;
    expect(field.value).toBe("Branches");
    expect([...field.options].map((o) => o.textContent)).toEqual(["No group", "Branches", "Datacentre", "New group…"]);
    await user.selectOptions(field, "New group…");
    await user.type(screen.getByRole("textbox", { name: "New group name" }), " Edge   sites ");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1]).toMatchObject({ group_name: "Edge sites" });
  });
});

describe("latencies of a second or more", () => {
  it("switch from milliseconds to seconds at 1000 ms", async () => {
    const { fmtMs, fmtMsCell, fmtMsJoin, fmtMsTick } = await import("../src/utils");
    expect(fmtMs(12.34)).toBe("12.3 ms");
    expect(fmtMs(999.94)).toBe("999.9 ms");
    // Judged after rounding: never "1000.0 ms".
    expect(fmtMs(999.96)).toBe("1.00 s");
    expect(fmtMs(1234)).toBe("1.23 s");
    expect(fmtMs(48_640)).toBe("48.6 s");
    expect(fmtMs(7458464799.7)).toBe("7458465 s");
    expect(fmtMs(null)).toBe("–");
    expect(fmtMsCell(42.2)).toBe("42.2");
    expect(fmtMsCell(2500)).toBe("2.50 s");
    expect(fmtMsJoin([10, 15])).toBe("10.0 / 15.0 ms");
    expect(fmtMsJoin([10, 1500], { sep: " · ", labels: ["best", "worst"] })).toBe("best 10.0 ms · worst 1.50 s");
    expect(fmtMsTick(250)).toBe("250 ms");
    expect(fmtMsTick(2500)).toBe("2.5 s");
  });

  it("prints a card's latency, averages and jitter in seconds", async () => {
    const slow: Target = {
      ...target, type: "ping",
      latest_run: { ...target.latest_run!, avg_ms: 7458464799.7, jitter_avg_ms: 1520 },
      stats_24h: { ...target.stats_24h, avg_ms: 2400, median_ms: 980 },
    };
    vi.spyOn(api, "targets").mockResolvedValue([slow]);
    vi.spyOn(api, "overview").mockResolvedValue({ targets: [], bucket_sec: 600, range_sec: 86400 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    const card = await screen.findByRole("article", { name: "Office WAN" });
    expect(card.textContent).toContain("7458465s");
    expect(within(card).getByTestId("card-latency-24h").textContent).toBe("24h avg 2.40 s24h median 980.0 ms");
    expect(card.textContent).toContain("Jitter1.52s");
    // The Mean and Median latency tiles.
    expect(screen.getAllByText("7458465 s")).toHaveLength(2);
  });
});
