import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type Hop, type HopSummaryEntry, type Target, type RunDetail, type ProbeType, type ProbeOptions } from "../src/api";
import { TargetActions } from "../src/components/TargetActions";
import { HelpTip } from "../src/components/Popover";
import { HopTable } from "../src/components/HopTable";
import { Sparkline } from "../src/components/Sparkline";
import { HeatLegend } from "../src/components/HopHeatmap";
import { TargetDetail } from "../src/pages/TargetDetail";
import { Dashboard } from "../src/pages/Dashboard";

const timestamp = "2026-09-17T12:00:00Z";
const hop: Hop = {
  hop_no: 1, ip: "192.0.2.1", hostname: "router.example", asn: "AS64500",
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
  tags: ["wan"], interval_sec: 60, count: 10, probe_interval: 1, protocol: "icmp", port: null,
  packet_size: 64, ip_version: "auto", max_hops: 30, enabled: true, notify: true, alert_loss_pct: 5,
  alert_latency_ms: 200, created_at: timestamp, updated_at: timestamp, next_run_at: null,
  last_status: "up", latest_run: run, stats_24h: { runs: 1, availability_pct: 100, avg_ms: 12, loss_pct: 0, route_changes: 0 },
  sparkline: [{ t: timestamp, avg: 12, loss: 0, reached: true }],
  timeline: { bucket_sec: 1800, since: timestamp, buckets: [{ s: "up", n: 1, avg: 12 }] },
};

function mockDetail(t: Target = target) {
  vi.spyOn(api, "target").mockResolvedValue(t);
  vi.spyOn(api, "series").mockResolvedValue({ points: [], bucket_sec: null, range_sec: 86400 });
  vi.spyOn(api, "runs").mockResolvedValue({ total: 1, items: [run] });
  vi.spyOn(api, "run").mockResolvedValue(run);
  vi.spyOn(api, "hopHistory").mockResolvedValue({ runs: [], max_hops: 0 });
  vi.spyOn(api, "hopSummary").mockResolvedValue({ total_runs: 0, hops: [] });
  vi.spyOn(api, "targetEvents").mockResolvedValue([]);
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

describe("hop inspection", () => {
  it("shows every original metric immediately even with a saved compact preference", () => {
    localStorage.setItem("mtr-tracker.hop-columns", JSON.stringify("compact"));
    const detailedHop = { ...hop, hostname: "long-router-name.branch-office.network.example", loss_pct: 5, sent: 20, received: 19, last_ms: 13, jitter_avg_ms: 2.5 };
    render(<HopTable hops={[detailedHop]} dstIp={hop.ip} />);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(["#", "Host", "ASN", "Loss", "Snt", "Rcv", "Last", "Avg", "Best", "Wrst", "StDev", "Jitter", "Jmax", "Latency"]);
    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
    expect(within(cells[1]).getByText(detailedHop.hostname)).toBeTruthy();
    expect(within(cells[1]).getByText(hop.ip!)).toBeTruthy();
    expect(cells.slice(2, 13).map((cell) => cell.textContent)).toEqual(["AS64500", "5.0%", "20", "19", "13.0", "12.0", "10.0", "15.0", "2.0", "2.5", "3.0"]);
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
    expect(within(navigation).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Current path", "Path history", "Path summary · 24h", "Runs", "Events"]);
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
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(["Started", "Result", "Hops", "Loss", "Avg", "Best", "Wrst", "StDev", "Jitter", "Duration", ""]);
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

  it("restores a remembered path summary with all metrics and expandable alternate addresses", async () => {
    const user = userEvent.setup();
    localStorage.setItem("mtr-tracker.tab", JSON.stringify("summary"));
    mockDetail();
    const primary: HopSummaryEntry = { ip: hop.ip, hostname: hop.hostname, asn: hop.asn, runs: 8, share_pct: 80, loss_pct: 1, max_loss_pct: 5, avg_ms: 12, best_ms: 10, worst_ms: 15, stdev_ms: 2, jitter_ms: 3, jitter_max_ms: 4 };
    vi.mocked(api.hopSummary).mockResolvedValue({ total_runs: 10, hops: [{ hop: 1, primary, alternates: [{ ...primary, ip: "192.0.2.2", hostname: "alternate.example", asn: "AS64501", runs: 2, share_pct: 20 }], silent_runs: 3 }] });
    renderDetail();
    const table = await screen.findByRole("table");
    expect(screen.getByRole("tab", { name: "Path summary · 24h" }).getAttribute("aria-selected")).toBe("true");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(["#", "Host", "ASN", "Seen", "Loss", "Max loss", "Avg", "Best", "Wrst", "StDev", "Jitter", "Latency"]);
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    // Runs where the hop answered nothing are shown as silence on the primary row, never as an extra address.
    expect(within(within(table).getAllByRole("row")[1]).getByText("3 silent")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "1 alternate address seen at this hop" }));
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
