import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type RunDetail, type Target, type TargetInput } from "../src/api";
import { TargetDetail } from "../src/pages/TargetDetail";
import { TargetForm } from "../src/components/TargetForm";
import { CheckDetails } from "../src/components/CheckDetails";
import { USER_AGENT_GROUPS, USER_AGENT_PRESETS } from "../src/components/HeaderEditor";
import { RangePicker } from "../src/components/RangePicker";
import { fmtDuration, timeTicks } from "../src/utils";

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

describe("target page polling", () => {
  it("fetches hop history and the hop summary only while their panels are on screen", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "target").mockResolvedValue(target);
    vi.spyOn(api, "series").mockResolvedValue({ points: [], bucket_sec: null, range_sec: 86400, route_changes: { stored: 0, marked: 0, hidden: 0, memory: 20 } });
    const runs = vi.spyOn(api, "runs").mockResolvedValue({ total: 1, items: [run] });
    vi.spyOn(api, "run").mockResolvedValue(run);
    const history = vi.spyOn(api, "hopHistory").mockResolvedValue({ runs: [], max_hops: 0 });
    const summary = vi.spyOn(api, "hopSummary").mockResolvedValue({ total_runs: 0, hops: [] });
    vi.spyOn(api, "targetEvents").mockResolvedValue([]);
    vi.spyOn(api, "routes").mockResolvedValue({ routes: [], segments: [], since: timestamp, range_sec: 86400, total_runs: 0 });
    vi.spyOn(api, "hourly").mockResolvedValue({ hours: [], range_sec: 86400 });
    vi.spyOn(api, "geo").mockResolvedValue({ enabled: false, ip_api_enabled: false, available: false, asn_available: false, run_id: null, sources: [], hops: [], destination: null });
    localStorage.setItem("mtr-tracker.tab", JSON.stringify("path"));
    render(<MemoryRouter initialEntries={["/targets/1"]}><Routes><Route path="/targets/:id" element={<TargetDetail />} /></Routes></MemoryRouter>);

    await screen.findByRole("tab", { name: "Path history" });
    const listCalls = () => runs.mock.calls.filter(([, q]) => q?.limit !== 1).length;  // limit 1 is the latest-run lookup
    expect(history).not.toHaveBeenCalled();
    expect(summary).not.toHaveBeenCalled();
    expect(listCalls()).toBe(0);

    await user.click(screen.getByRole("tab", { name: "Path history" }));
    await waitFor(() => expect(history).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("tab", { name: /Path summary/ }));
    await waitFor(() => expect(summary).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(listCalls()).toBe(1));
    expect(history).toHaveBeenCalledTimes(1);
  });
});

describe("DNS transport", () => {
  async function openDnsForm(onSubmit: (v: TargetInput) => Promise<void>) {
    const user = userEvent.setup();
    render(<MemoryRouter><TargetForm open prefill={{ name: "Resolver", host: "example.com" }} onClose={() => undefined} onSubmit={onSubmit} submitting={false} /></MemoryRouter>);
    await user.click(screen.getByRole("radio", { name: "DNS" }));
    return user;
  }

  it("requires a resolver for DNS over TLS and HTTPS and submits the chosen transport", async () => {
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openDnsForm(onSubmit);
    const transport = screen.getByRole("combobox", { name: "DNS transport" });
    expect((transport as HTMLSelectElement).value).toBe("udp");

    await user.selectOptions(transport, "dot");
    expect((screen.getByRole("checkbox", { name: /Verify the resolver's certificate/ }) as HTMLInputElement).checked).toBe(true);
    await user.click(screen.getByRole("button", { name: "Add target" }));
    expect(await screen.findByText(/needs a resolver/)).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();

    await user.selectOptions(transport, "doh");
    expect(screen.queryByRole("spinbutton", { name: "Resolver port" })).toBeNull();  // DoH takes the port from the URL
    await user.type(screen.getByRole("textbox", { name: "Resolver" }), "https://dns.example/dns-query");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options).toMatchObject({ transport: "doh", resolver: "https://dns.example/dns-query", verify_tls: true });
  });

  it("explains the chosen transport and fills a public resolver for DoT and DoH", async () => {
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openDnsForm(onSubmit);
    expect(screen.queryByRole("group", { name: "Public resolvers" })).toBeNull();  // plain DNS keeps the system resolver
    const transport = screen.getByRole("combobox", { name: "DNS transport" });

    await user.selectOptions(transport, "dot");
    expect(screen.getByTestId("dns-transport-help").textContent).toMatch(/DNS over TLS on port 853/);
    await user.click(screen.getByRole("button", { name: "Cloudflare" }));
    expect((screen.getByRole("textbox", { name: "Resolver" }) as HTMLInputElement).value).toBe("one.one.one.one");

    await user.selectOptions(transport, "doh");
    await user.click(screen.getByRole("button", { name: "Google" }));
    expect(screen.getByRole("button", { name: "Google" }).getAttribute("aria-pressed")).toBe("true");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options).toMatchObject({ transport: "doh", resolver: "https://dns.google/dns-query" });
  });

  it("keeps TCP on port 53 against the system resolver without asking for one", async () => {
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openDnsForm(onSubmit);
    await user.selectOptions(screen.getByRole("combobox", { name: "DNS transport" }), "tcp");
    expect(screen.queryByRole("checkbox", { name: /Verify the resolver's certificate/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options).toMatchObject({ transport: "tcp", resolver: "" });
  });

  it("shows the transport, the server that answered and a non-standard port in the run details", () => {
    const dnsRun = { ...run, target_type: "dns", details: { record_type: "A", resolver: "dns.example", nameserver: "192.0.2.53", transport: "dot", port: 8853, answers: ["192.0.2.10"], rcode: "NOERROR", ttl: 60 } } as RunDetail;
    render(<CheckDetails run={dnsRun} type="dns" />);
    expect(screen.getByText("DNS over TLS (DoT, port 853) · port 8853")).toBeTruthy();
    expect(screen.getByText(/192\.0\.2\.53/)).toBeTruthy();
  });
});

describe("HTTP JSON query", () => {
  async function openHttpForm(onSubmit: (v: TargetInput) => Promise<void>) {
    const user = userEvent.setup();
    render(<MemoryRouter><TargetForm open prefill={{ name: "Claude status", host: "https://status.claude.com/api/v2/summary.json" }} onClose={() => undefined} onSubmit={onSubmit} submitting={false} /></MemoryRouter>);
    await user.click(screen.getByRole("radio", { name: "HTTP(S)" }));
    return user;
  }

  it("takes a JSONata expression, a condition and the expected value", async () => {
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openHttpForm(onSubmit);
    expect(screen.queryByRole("combobox", { name: "JSON condition" })).toBeNull();  // shown once there is a query

    await user.click(screen.getByRole("textbox", { name: "JSON query (optional)" }));
    await user.paste('components[id = "yyzkbfz2thpt"].status');
    const condition = screen.getByRole("combobox", { name: "JSON condition" });
    expect((condition as HTMLSelectElement).value).toBe("==");

    await user.selectOptions(condition, ">=");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    expect(await screen.findByText(/needs an expected value/)).toBeTruthy();
    await user.type(screen.getByRole("textbox", { name: "Expected value" }), "operational");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    expect(await screen.findByText(/compares numbers/)).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();

    await user.selectOptions(condition, "==");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options).toMatchObject({ json_query: 'components[id = "yyzkbfz2thpt"].status', json_operator: "==", json_expected: "operational" });
  });

  it("shows the query, what it returned and the condition in the run details", () => {
    const httpRun = { ...run, target_type: "http", details: { status: 200, status_ok: true, json_query: 'components[id = "x"].status', json_operator: "==", json_expected: "operational", json_value: "major_outage", json_ok: false } } as RunDetail;
    render(<CheckDetails run={httpRun} type="http" />);
    expect(screen.getByText('components[id = "x"].status')).toBeTruthy();
    expect(screen.getByTestId("json-result").textContent).toBe('"major_outage" (== operational)');
  });
});

describe("tap responsiveness", () => {
  it("rounds durations without impossible remainders", () => {
    expect(fmtDuration(6 * 86400 + 23.7 * 3600)).toBe("7d");
    expect(fmtDuration(3599.6)).toBe("1h");
    expect(fmtDuration(59.6)).toBe("1m");
    expect(fmtDuration(380)).toBe("6m 20s");
    expect(fmtDuration(2 * 86400 + 19 * 3600)).toBe("2d 19h");
    expect(fmtDuration(86399)).toBe("1d");
  });

  it("gives a time axis a handful of round ticks instead of one per data point", () => {
    const end = Date.UTC(2026, 9, 1, 12, 0);
    const day = timeTicks([end - 86_400_000, end], 300);
    expect(day.length).toBeGreaterThan(1);
    expect(day.length).toBeLessThanOrEqual(5);
    const step = day[1] - day[0];
    expect(step % 3_600_000).toBe(0);
    expect(day.every((t) => t >= end - 86_400_000 && t <= end)).toBe(true);
    const month = timeTicks([end - 30 * 86_400_000, end], 300);
    expect(month.length).toBeGreaterThan(1);
    expect(month.length).toBeLessThanOrEqual(5);
  });

  it("lights up a range button on the tap and hands the change on", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RangePicker value="24h" onChange={onChange} />);
    await user.click(screen.getByRole("tab", { name: "7d" }));
    expect(screen.getByRole("tab", { name: "7d" }).getAttribute("aria-selected")).toBe("true");
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("7d"));
  });
});

describe("HTTP request headers and cache buster", () => {
  async function openHttpForm(onSubmit: (v: TargetInput) => Promise<void>, initial?: Target) {
    const user = userEvent.setup();
    render(<MemoryRouter><TargetForm open initial={initial} prefill={initial ? undefined : { name: "Shop", host: "shop.example/status?lang=en" }} onClose={() => undefined} onSubmit={onSubmit} submitting={false} /></MemoryRouter>);
    if (!initial) await user.click(screen.getByRole("radio", { name: "HTTP(S)" }));
    return user;
  }

  it("explains headers, offers User-Agent presets and quick additions and submits the rows", async () => {
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openHttpForm(onSubmit);
    expect(screen.getByTestId("headers-help").textContent).toMatch(/identifies itself as MTR-Tracker\/1\.0/);

    await user.selectOptions(screen.getByRole("combobox", { name: "User-Agent preset" }), "User-Agent: curl");
    expect((screen.getByRole("combobox", { name: "Header 1 name" }) as HTMLInputElement).value).toBe("User-Agent");
    expect((screen.getByRole("textbox", { name: "Header 1 value" }) as HTMLInputElement).value).toBe("curl/8.15.0");
    await user.click(screen.getByRole("button", { name: "Accept JSON" }));
    await user.click(screen.getByRole("button", { name: "Add header" }));
    await user.type(screen.getByRole("combobox", { name: "Header 3 name" }), "X-Team");
    await user.type(screen.getByRole("textbox", { name: "Header 3 value" }), "noc");
    // Editing the value by hand turns the preset picker to "custom".
    await user.type(screen.getByRole("textbox", { name: "Header 1 value" }), " extra");
    expect((screen.getByRole("combobox", { name: "User-Agent preset" }) as HTMLSelectElement).value).toBe("custom");

    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options.headers).toEqual({ "User-Agent": "curl/8.15.0 extra", Accept: "application/json", "X-Team": "noc" });
    expect(onSubmit.mock.calls[0][0].options.cache_buster).toBe(false);
  });

  it("groups the User-Agent presets and fills any of them", async () => {
    expect(new Set(USER_AGENT_PRESETS.map((p) => p.value)).size).toBe(USER_AGENT_PRESETS.length);  // the dropdown is keyed on the value
    expect(new Set(USER_AGENT_PRESETS.map((p) => p.label)).size).toBe(USER_AGENT_PRESETS.length);
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openHttpForm(onSubmit);
    const select = screen.getByRole("combobox", { name: "User-Agent preset" });
    expect([...select.querySelectorAll("optgroup")].map((g) => g.label)).toEqual([...USER_AGENT_GROUPS]);
    for (const group of USER_AGENT_GROUPS) expect(USER_AGENT_PRESETS.some((p) => p.group === group)).toBe(true);
    await user.selectOptions(select, "User-Agent: Googlebot");
    expect((screen.getByRole("textbox", { name: "Header 1 value" }) as HTMLInputElement).value).toBe("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)");
    await user.selectOptions(select, "User-Agent: Chrome on Android");
    expect((select as HTMLSelectElement).value).toMatch(/Android 10; K.*Mobile Safari/);
    expect(screen.queryByRole("textbox", { name: "Header 2 value" })).toBeNull();  // replaced, not a second row
  });

  it("refuses invalid and duplicate header names", async () => {
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openHttpForm(onSubmit);
    await user.type(screen.getByRole("combobox", { name: "Header 1 name" }), "User Agent");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    expect(await screen.findByText(/"User Agent" is not a valid header name/)).toBeTruthy();
    await user.clear(screen.getByRole("combobox", { name: "Header 1 name" }));
    await user.type(screen.getByRole("combobox", { name: "Header 1 name" }), "accept");
    await user.click(screen.getByRole("button", { name: "Accept JSON" }));
    await user.click(screen.getByRole("button", { name: "Add header" }));
    await user.type(screen.getByRole("combobox", { name: "Header 2 name" }), "Accept");
    await user.click(screen.getByRole("button", { name: "Add target" }));
    expect(await screen.findByText(/listed twice/)).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Remove header 2" }));
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options.headers).toEqual({ accept: "application/json" });
  });

  it("explains the cache buster with the URL a run would request and saves the switch", async () => {
    const onSubmit = vi.fn(async (_values: TargetInput) => undefined);
    const user = await openHttpForm(onSubmit);
    expect(screen.getByTestId("cache-buster-help").textContent).toMatch(/CDNs, proxies and server caches/);
    expect(screen.getByTestId("cache-buster-example").textContent).toBe("https://shop.example/status?lang=en&mtr_tracker_cachebuster=3f9c2a71b0d4e8a6");
    await user.click(screen.getByRole("button", { name: "About the cache buster" }));
    expect(await screen.findByText(/Uptime Kuma's cache buster/)).toBeTruthy();
    await user.click(screen.getByRole("checkbox", { name: "Cache buster" }));
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].options).toMatchObject({ cache_buster: true, headers: {} });
  });

  it("loads the stored headers when editing", async () => {
    const http = { ...target, type: "http", host: "https://shop.example/", options: { headers: { "User-Agent": "Probe/2", Accept: "text/html" }, cache_buster: true } } as unknown as Target;
    await openHttpForm(async () => undefined, http);
    expect((screen.getByRole("textbox", { name: "Header 2 value" }) as HTMLInputElement).value).toBe("text/html");
    expect((screen.getByRole("checkbox", { name: "Cache buster" }) as HTMLInputElement).checked).toBe(true);
  });

  it("shows the requested URL and the headers a run sent", () => {
    const httpRun = { ...run, target_type: "http", details: { url: "https://shop.example/", request_url: "https://shop.example/?mtr_tracker_cachebuster=ab12", cache_buster: true, request_headers: { "User-Agent": "Probe/2", Authorization: "********" }, status: 200, status_ok: true } } as RunDetail;
    render(<CheckDetails run={httpRun} type="http" />);
    expect(screen.getByTestId("request-url").textContent).toMatch(/mtr_tracker_cachebuster=ab12/);
    expect(screen.getByTestId("request-headers").textContent).toBe("User-Agent: Probe/2Authorization: ********");
  });
});
