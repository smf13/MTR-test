import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type Hop, type Target, type RunDetail, type ProbeType, type ProbeOptions } from "../src/api";
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
  packet_size: 64, ip_version: "auto", max_hops: 30, enabled: true, alert_loss_pct: 5,
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
}
function renderDetail() {
  return render(<MemoryRouter initialEntries={["/targets/1"]}><Routes><Route path="/targets/:id" element={<TargetDetail />} /></Routes></MemoryRouter>);
}

describe("target actions", () => {
  it("supports keyboard navigation, invokes the intended action, and restores focus on Escape", async () => {
    const user = userEvent.setup();
    const clone = vi.fn();
    const remove = vi.fn();
    render(<TargetActions name="Office WAN" enabled onToggle={vi.fn()} onEdit={vi.fn()} onClone={clone} onDelete={remove} />);
    const trigger = screen.getByRole("button", { name: "Actions for Office WAN" });
    await user.click(trigger);
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Pause" }));
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
  it("keeps key metrics and destination visible, with persistent access to every metric", async () => {
    const user = userEvent.setup();
    const view = render(<HopTable hops={[hop]} dstIp={hop.ip} />);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent?.trim())).toEqual(["#", "Host", "Loss", "Avg (ms)", "Worst (ms)", "Jitter (ms)"]);
    expect(screen.getByText("dst")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "All metrics" }));
    expect(screen.getByRole("columnheader", { name: "ASN" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Jmax (ms)" })).toBeTruthy();
    view.unmount();
    render(<HopTable hops={[hop]} />);
    expect(screen.getByRole("columnheader", { name: "ASN" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Compact" }));
    expect(screen.queryByRole("columnheader", { name: "ASN" })).toBeNull();
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
  it("opens path data before its chart and keeps Runs and Events independent of overview charts", async () => {
    const user = userEvent.setup();
    mockDetail();
    renderDetail();
    const navigation = await screen.findByRole("tablist", { name: "Target views" });
    const overview = within(navigation).getByRole("tab", { name: "Overview" });
    expect(overview.getAttribute("aria-selected")).toBe("true");
    await user.click(within(navigation).getByRole("tab", { name: "Path analysis" }));
    const table = await screen.findByRole("table");
    const profile = screen.getByRole("heading", { name: /Path profile/ });
    expect(table.compareDocumentPosition(profile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Latency distribution/ })).toBeNull();
    await user.click(within(navigation).getByRole("tab", { name: "Runs" }));
    expect(screen.getByRole("button", { name: "Route changes" })).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    expect(within(navigation).getByRole("tab", { name: "Events" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("heading", { name: /Path profile/ })).toBeNull();
    await user.keyboard("{Home}");
    expect(overview.getAttribute("aria-selected")).toBe("true");
  });

  it.each<[ProbeType, ProbeOptions, boolean]>([
    ["mtr", {}, true], ["ping", {}, false], ["http", {}, false], ["tcp", {}, false], ["dns", {}, false],
    ["globalping", { measurement: "mtr" }, true], ["globalping", { measurement: "traceroute" }, true],
    ["globalping", { measurement: "http" }, false],
  ])("preserves applicable views for %s %j", async (type, options, path) => {
    localStorage.setItem("mtr-tracker.detail-view", JSON.stringify("path"));
    mockDetail({ ...target, type, options, latest_run: null });
    renderDetail();
    const navigation = await screen.findByRole("tablist", { name: "Target views" });
    expect(!!within(navigation).queryByRole("tab", { name: "Path analysis" })).toBe(path);
    expect(within(navigation).getByRole("tab", { name: path ? "Path analysis" : "Overview" }).getAttribute("aria-selected")).toBe("true");
  });
});

describe("dashboard", () => {
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
