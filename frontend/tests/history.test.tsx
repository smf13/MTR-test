import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type Event, type RunDetail, type Target, type TargetInput } from "../src/api";
import { EventsList } from "../src/components/EventsList";
import { TargetDetail } from "../src/pages/TargetDetail";
import { TargetForm } from "../src/components/TargetForm";
import { RunsTable } from "../src/components/RunsTable";
import { CheckDetails } from "../src/components/CheckDetails";

const timestamp = "2026-09-17T12:00:00Z";
const run: RunDetail = {
  id: 10, target_id: 1, target_name: "Office WAN", target_host: "192.0.2.1", target_type: "mtr",
  started_at: timestamp, finished_at: timestamp, duration_ms: 1000, status: "ok", error: null,
  src: "simulation", dst_ip: "192.0.2.1", reached: true, hop_count: 0, sent: 10,
  loss_pct: 0, last_ms: 12, avg_ms: 12, best_ms: 10, worst_ms: 15, stdev_ms: 2,
  jitter_avg_ms: 2, jitter_max_ms: 3, route_hash: "example", route_changed: false,
  command: null, details: null, hops: [], prev_run_id: null, next_run_id: null,
};
const target = {
  id: 1, name: "Office WAN", host: "192.0.2.1", type: "mtr", options: {}, description: "", tags: [], interval_sec: 60, count: 10,
  probe_interval: 1, protocol: "icmp", port: null, packet_size: 64, ip_version: "auto", max_hops: 30, enabled: true, notify: true,
  alert_loss_pct: 0, alert_latency_ms: 0, created_at: timestamp, updated_at: timestamp, next_run_at: timestamp, last_run_at: timestamp,
  last_status: "up", latest_run: run, running: false,
  stats_24h: { runs: 1, availability_pct: 100, avg_ms: 12, median_ms: 11, loss_pct: 0, route_changes: 0 },
  sparkline: [], timeline: { bucket_sec: 1800, since: timestamp, buckets: [] },
} as unknown as Target;

function mockTargetPage(runsTotal = { all: 5, failed: 2 }) {
  vi.spyOn(api, "target").mockResolvedValue(target);
  vi.spyOn(api, "series").mockResolvedValue({ points: [], bucket_sec: null, range_sec: 86400, route_changes: { stored: 0, marked: 0, hidden: 0, memory: 20 } });
  const second = { ...run, id: 11, status: "error", error: "keyword 'x' not found" } as RunDetail;
  vi.spyOn(api, "runs").mockImplementation(async (_id, q) => (q?.limit === 1 ? { total: q.status === "failed" ? runsTotal.failed : runsTotal.all, items: [run] } : { total: 2, items: [run, second] }));
  vi.spyOn(api, "run").mockResolvedValue(run);
  vi.spyOn(api, "hopHistory").mockResolvedValue({ runs: [], max_hops: 0 });
  vi.spyOn(api, "hopSummary").mockResolvedValue({ total_runs: 0, hops: [] });
  vi.spyOn(api, "targetEvents").mockResolvedValue([]);
  vi.spyOn(api, "routes").mockResolvedValue({ routes: [], segments: [], since: timestamp, range_sec: 86400, total_runs: 0 });
  vi.spyOn(api, "hourly").mockResolvedValue({ hours: [], range_sec: 86400 });
  vi.spyOn(api, "geo").mockResolvedValue({ enabled: false, ip_api_enabled: false, available: false, asn_available: false, run_id: null, sources: [], hops: [], destination: null });
  render(<MemoryRouter initialEntries={["/targets/1"]}><Routes><Route path="/targets/:id" element={<TargetDetail />} /></Routes></MemoryRouter>);
}

describe("clearing history", () => {
  it("asks which runs to clear, names the count and deletes only after confirmation", async () => {
    const user = userEvent.setup();
    const clear = vi.spyOn(api, "clearRuns").mockResolvedValue({ runs: 5, events: 3, status_reset: true });
    mockTargetPage();
    await user.click(await screen.findByRole("button", { name: "Actions for Office WAN" }));
    await user.click(screen.getByRole("menuitem", { name: "Clear history…" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Clear history of Office WAN?");
    expect((screen.getByRole("radio", { name: /Failed runs only/ }) as HTMLInputElement).checked).toBe(true);
    expect(await screen.findByRole("button", { name: "Delete 2 runs" })).toBeTruthy();
    await user.click(screen.getByRole("radio", { name: /Entire history/ }));
    await user.click(screen.getByRole("button", { name: "Delete 5 runs" }));
    await waitFor(() => expect(clear).toHaveBeenCalledWith(1, "all"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("cancelling deletes nothing, and an empty choice cannot be confirmed", async () => {
    const user = userEvent.setup();
    const clear = vi.spyOn(api, "clearRuns");
    mockTargetPage({ all: 3, failed: 0 });
    await user.click(await screen.findByRole("button", { name: "Actions for Office WAN" }));
    await user.click(screen.getByRole("menuitem", { name: "Clear history…" }));
    expect(((await screen.findByRole("button", { name: "Delete 0 runs" })) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(clear).not.toHaveBeenCalled();
  });

  it("deletes the runs ticked in the Runs tab after confirmation", async () => {
    const user = userEvent.setup();
    const del = vi.spyOn(api, "deleteRuns").mockResolvedValue({ runs: 1, events: 1, status_reset: false });
    localStorage.setItem("mtr-tracker.tab", JSON.stringify("runs"));
    mockTargetPage();
    const boxes = await screen.findAllByRole("checkbox", { name: /Select run of/ });
    expect(screen.queryByRole("button", { name: /Delete \d+ selected/ })).toBeNull();
    await user.click(boxes[1]);
    await user.click(screen.getByRole("button", { name: "Delete 1 selected" }));
    expect(del).not.toHaveBeenCalled();  // the confirmation comes first
    await user.click(screen.getByRole("button", { name: "Delete 1 run" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith(1, [11]));
  });
});

describe("runs table selection", () => {
  it("ticks rows without opening them and selects the whole page from the header", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onSelect = vi.fn();
    const runs = [run, { ...run, id: 11 }];
    const { rerender } = render(<RunsTable runs={runs} now={Date.now()} onOpen={onOpen} selected={new Set()} onSelect={onSelect} />);
    await user.click(screen.getAllByRole("checkbox", { name: /Select run of/ })[0]);
    expect(onSelect).toHaveBeenLastCalledWith([10], true);
    expect(onOpen).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox", { name: "Select all runs on this page" }));
    expect(onSelect).toHaveBeenLastCalledWith([10, 11], true);

    rerender(<RunsTable runs={runs} now={Date.now()} onOpen={onOpen} selected={new Set([10])} onSelect={onSelect} />);
    expect((screen.getByRole("checkbox", { name: "Select all runs on this page" }) as HTMLInputElement).indeterminate).toBe(true);
    rerender(<RunsTable runs={runs} now={Date.now()} onOpen={onOpen} />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);  // no selection props, no checkboxes
  });
});

describe("keyword regex", () => {
  it("submits the regex switch with the keyword", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    render(<MemoryRouter><TargetForm open prefill={{ name: "Portal", host: "https://portal.example.com/health" }} onClose={() => undefined} onSubmit={onSubmit} submitting={false} /></MemoryRouter>);
    await user.click(screen.getByRole("radio", { name: "HTTP(S)" }));
    await user.click(screen.getByRole("checkbox", { name: "Regular expression" }));
    await user.click(screen.getByRole("textbox", { name: "Keyword" }));
    await user.paste("(healthy|ok)\\b");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options).toMatchObject({ keyword: "(healthy|ok)\\b", keyword_regex: true });
  });

  it("shows the pattern and the text it matched in the run details", () => {
    const httpRun = { ...run, target_type: "http", details: { status: 200, status_ok: true, keyword: "version \\d+", keyword_regex: true, keyword_found: true, keyword_match: "Version 42" } } as RunDetail;
    render(<CheckDetails run={httpRun} type="http" />);
    expect(screen.getByText("Keyword regex")).toBeTruthy();
    expect(screen.getByTestId("keyword-result").textContent).toBe('/version \\d+/i matched "Version 42"');
  });
});

describe("events held back by the cooldown", () => {
  it("marks an event whose notification waited for the cooldown", () => {
    const base = { target_id: 1, target_name: "Office WAN", target_host: "192.0.2.1", run_id: null, created_at: timestamp };
    const events = [
      { ...base, id: 2, kind: "recovered", severity: "info", message: "Office WAN recovered (down -> up)", details: { notification: "held back" } },
      { ...base, id: 1, kind: "down", severity: "critical", message: "Office WAN is DOWN", details: {} },
    ] as unknown as Event[];
    render(<MemoryRouter><EventsList events={events} now={Date.now()} /></MemoryRouter>);
    expect(screen.getAllByText("notification held back by the cooldown")).toHaveLength(1);
  });
});

describe("latency across targets colours", () => {
  it("gives every target on the chart its own colour and line pattern", async () => {
    const { OverviewChart } = await import("../src/components/Visuals");
    const { seriesColor, seriesDash, SERIES_SLOTS, SERIES_DASHES } = await import("../src/utils");
    // Ids 1 and 13 shared a colour when it was picked by database id (modulo the palette size).
    const ids = [1, 13, 25, 2, 14, 3, 4, 5, 6, 7, 8, 9];
    const data = { range_sec: 86400, bucket_sec: 600, targets: ids.map((id) => ({ id, name: `T${id}`, host: "192.0.2.1", enabled: true, points: [{ t: timestamp, avg: id, loss: 0, ok: true, n: 1 }] })) };
    const { container } = render(<OverviewChart data={data} />);
    const swatches = [...container.querySelectorAll("svg[data-series-color]")].map((el) => `${el.getAttribute("data-series-color")}|${el.getAttribute("data-series-dash")}`);
    expect(swatches).toHaveLength(ids.length);
    expect(new Set(swatches).size).toBe(ids.length);
    expect(swatches.slice(0, 8).every((s) => s.endsWith("|"))).toBe(true);  // the first eight are solid lines
    expect(swatches[8]).toBe("var(--series-1)|6 4");                     // the ninth repeats the first colour, dashed

    // Unique up to slots × patterns.
    const n = SERIES_SLOTS * SERIES_DASHES.length;
    expect(new Set(Array.from({ length: n }, (_, i) => `${seriesColor(i)}|${seriesDash(i)}`)).size).toBe(n);
  });
});
