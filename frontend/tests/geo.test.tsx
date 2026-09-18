import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { api, type GeoIpStatus, type GeoPoint, type PathGeo, type Settings as SettingsT } from "../src/api";
import { PathMapCard, buildStops, hopRanges } from "../src/components/PathMapCard";
import { Settings } from "../src/pages/Settings";
import type { MapStop } from "../src/components/PathMapCard";

// Leaflet needs a real layout; the card's own logic (stops, captions, unlocated hops) is what these tests cover.
vi.mock("../src/components/PathMap", () => ({
  default: ({ stops, paths }: { stops: MapStop[]; paths: [number, number][][] }) => (
    <div data-testid="path-map" data-paths={paths.length}>{stops.map((s) => `${s.id}:${s.kind}`).join(" ")}</div>
  ),
}));

const berlin: GeoPoint = { lat: 52.52, lon: 13.405, city: "Berlin", region: null, country: "Germany", country_code: "DE", accuracy_km: 50 };
const frankfurt: GeoPoint = { lat: 50.11, lon: 8.68, city: "Frankfurt", region: "Hesse", country: "Germany", country_code: "DE", accuracy_km: 20 };
const london: GeoPoint = { lat: 51.5, lon: -0.12, city: "London", region: null, country: "United Kingdom", country_code: "GB", accuracy_km: 20 };

const geo: PathGeo = {
  enabled: true,
  available: true,
  run_id: 10,
  sources: [{ kind: "public_ip", label: "This server (public address)", ip: "198.51.100.7", geo: berlin, note: null }],
  hops: [
    { hop_no: 1, ip: "192.168.1.1", hostname: null, asn: null, avg_ms: 0.4, loss_pct: 0, geo: null, note: "private address" },
    { hop_no: 2, ip: "10.0.0.1", hostname: null, asn: null, avg_ms: 1.2, loss_pct: 0, geo: null, note: "private address" },
    { hop_no: 3, ip: "203.0.113.1", hostname: "core1.example", asn: "AS64500", avg_ms: 8, loss_pct: 0, geo: frankfurt, note: null },
    { hop_no: 4, ip: "203.0.113.2", hostname: "core2.example", asn: "AS64500", avg_ms: 8.5, loss_pct: 10, geo: { ...frankfurt, lat: 50.12 }, note: null },
    { hop_no: 5, ip: "203.0.113.9", hostname: null, asn: null, avg_ms: 15, loss_pct: 0, geo: null, note: "not in database" },
    { hop_no: 6, ip: "192.0.2.1", hostname: "www.example", asn: "AS64501", avg_ms: 20, loss_pct: 0, geo: london, note: null },
  ],
  destination: { ip: "192.0.2.1", host: "www.example", reached: true, geo: london, note: null },
};

describe("path map", () => {
  it("groups consecutive hops in one place, ends the line at the destination and formats hop ranges", () => {
    const { stops, paths } = buildStops(geo);
    expect(stops.map((s) => [s.id, s.kind])).toEqual([["src-0", "source"], ["hop-3", "hop"], ["dst", "destination"]]);
    expect(stops[1].hopNos).toEqual([3, 4]);
    expect(stops[1].lines).toEqual(["3. core1.example (203.0.113.1) · AS64500 · 8.0 ms", "4. core2.example (203.0.113.2) · AS64500 · 8.5 ms · 10.0% loss"]);
    expect(stops[1].place).toBe("Frankfurt, Hesse, Germany");
    expect(stops[2].title).toBe("Destination · www.example");
    expect(paths).toEqual([[[52.52, 13.405], [50.11, 8.68], [51.5, -0.12]]]);
    expect(hopRanges([5, 1, 2, 3, 8])).toBe("1–3, 5, 8");

    // A ping-style probe has no hops: the destination is placed on its own, one line per located source.
    const direct = buildStops({ ...geo, hops: [], sources: [geo.sources[0], { kind: "probe", label: "Frankfurt, DE", ip: null, geo: frankfurt, note: null }] });
    expect(direct.stops.map((s) => s.kind)).toEqual(["source", "source", "destination"]);
    expect(direct.paths).toHaveLength(2);
  });

  it("shows the map with a located-hop caption and lists the hops it could not place", async () => {
    render(<MemoryRouter><PathMapCard geo={geo} pathProbe /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: /Path map/ })).toBeTruthy();
    expect(screen.getByText("Latest run · 3 of 6 hops located")).toBeTruthy();
    // The map chunk is lazy-loaded, so it appears after the suspense fallback.
    expect((await screen.findByTestId("path-map")).textContent).toBe("src-0:source hop-3:hop dst:destination");
    const notes = screen.getByText("Not located:").parentElement!;
    expect(notes.textContent).toContain("hops 1–2 · private address");
    expect(notes.textContent).toContain("hop 5 · not in database");
  });

  it("points to Settings without a key and explains a database that is still missing", () => {
    const { rerender } = render(<MemoryRouter><PathMapCard geo={{ ...geo, enabled: false }} pathProbe /></MemoryRouter>);
    expect(screen.getByText(/add a MaxMind licence key under/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
    expect(screen.queryByTestId("path-map")).toBeNull();
    rerender(<MemoryRouter><PathMapCard geo={{ ...geo, available: false }} pathProbe={false} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: /Location map/ })).toBeTruthy();
    expect(screen.getByText(/has not been downloaded yet/)).toBeTruthy();
    expect(screen.queryByTestId("path-map")).toBeNull();
  });
});

const settings: SettingsT = {
  retention_days: 30, asn_lookup: true, reverse_dns: true, webhook_url: "", webhook_events: [], pushover_enabled: false,
  pushover_user_key: "", pushover_api_token: "", pushover_device: "", pushover_sound: "", pushover_priority: "auto", pushover_events: [],
  base_url: "", site_name: "MTR Tracker", tag_colors: {}, globalping_token: "", maxmind_account_id: "", maxmind_license_key: "",
};
const geoipStatus: GeoIpStatus = {
  configured: false, available: false, simulated: false, edition: "GeoLite2-City", path: "/data/geoip/GeoLite2-City.mmdb", build_epoch: null,
  downloaded_at: null, last_attempt: null, last_success: null, last_error: null, updating: false, refresh_after_sec: 604800,
};

describe("settings · MaxMind GeoIP", () => {
  it("saves the credentials and downloads the database on demand", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "settings").mockResolvedValue(settings);
    vi.spyOn(api, "status").mockRejectedValue(new Error("offline"));
    vi.spyOn(api, "tags").mockResolvedValue([]);
    const statusMock = vi.spyOn(api, "geoipStatus").mockResolvedValue(geoipStatus);
    const update = vi.spyOn(api, "updateSettings").mockImplementation(async (patch) => ({ ...settings, ...patch } as SettingsT));
    const download = vi.spyOn(api, "geoipUpdate").mockResolvedValue({ ...geoipStatus, configured: true, available: true, build_epoch: "2026-09-01T00:00:00Z" });
    render(<MemoryRouter><Settings /></MemoryRouter>);

    const section = (await screen.findByRole("heading", { name: "MaxMind GeoIP" })).closest("section")!;
    expect(within(section).getByText("No licence key saved. Target pages show no map.")).toBeTruthy();
    const downloadButton = within(section).getByRole("button", { name: "Download now" });
    expect(downloadButton.hasAttribute("disabled")).toBe(true);

    await user.type(within(section).getByLabelText("MaxMind licence key"), "lic_key_1234");
    await user.type(within(section).getByLabelText("MaxMind account ID"), "123456");
    statusMock.mockResolvedValue({ ...geoipStatus, configured: true });
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith(expect.objectContaining({ maxmind_license_key: "lic_key_1234", maxmind_account_id: "123456" })));
    await waitFor(() => expect(downloadButton.hasAttribute("disabled")).toBe(false));
    expect(within(section).getByText(/Database not downloaded yet/)).toBeTruthy();

    statusMock.mockResolvedValue({ ...geoipStatus, configured: true, available: true, build_epoch: "2026-09-01T00:00:00Z", downloaded_at: new Date().toISOString() });
    await user.click(downloadButton);
    await waitFor(() => expect(download).toHaveBeenCalledOnce());
    expect(await within(section).findByText(/built 2026-09-01/)).toBeTruthy();
  });
});
