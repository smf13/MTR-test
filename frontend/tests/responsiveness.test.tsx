import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type RunDetail, type Target, type TargetInput } from "../src/api";
import { TargetDetail } from "../src/pages/TargetDetail";
import { TargetForm } from "../src/components/TargetForm";
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
  stats_24h: { runs: 1, availability_pct: 100, avg_ms: 12, loss_pct: 0, route_changes: 0 },
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
