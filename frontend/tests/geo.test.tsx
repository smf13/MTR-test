import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { api, type GeoIpStatus, type GeoPoint, type PathGeo, type Settings as SettingsT } from "../src/api";
import { PathMapCard, buildStops, hopRanges } from "../src/components/PathMapCard";
import { Settings } from "../src/pages/Settings";
import { GeoLookup } from "../src/pages/GeoLookup";
import type { MapFocus, MapPlace } from "../src/components/PathMapCard";

// Leaflet needs a real layout; the card's own logic (stops, places, captions, unlocated hops, route steps) is what
// these tests cover. The stub reports the place it was asked to focus and lets a test click a marker.
vi.mock("../src/components/PathMap", () => ({
  default: ({ places, paths, focus, onSelect }: { places: MapPlace[]; paths: [number, number][][]; focus?: MapFocus | null; onSelect?: (id: string) => void; height?: number }) => (
    <div data-testid="path-map" data-paths={paths.length} data-focus={focus?.id ?? ""}>
      {places.map((p) => <button key={p.id} type="button" onClick={() => onSelect?.(p.id)}>{`${p.kind}[${p.label}]`}</button>)}
    </div>
  ),
}));

const berlin: GeoPoint = { lat: 52.52, lon: 13.405, city: "Berlin", region: null, country: "Germany", country_code: "DE", accuracy_km: 50 };
const frankfurt: GeoPoint = { lat: 50.11, lon: 8.68, city: "Frankfurt", region: "Hesse", country: "Germany", country_code: "DE", accuracy_km: 20 };
const london: GeoPoint = { lat: 51.5, lon: -0.12, city: "London", region: null, country: "United Kingdom", country_code: "GB", accuracy_km: 20 };

const geo: PathGeo = {
  enabled: true,
  available: true,
  asn_available: true,
  run_id: 10,
  sources: [{ kind: "public_ip", label: "This server (public address)", ip: "198.51.100.7", geo: berlin, note: null, evidence: "placed by the public address it is seen from, 198.51.100.7, not by its own address 192.168.1.20", asn: "AS64499", as_name: "Home ISP AG" }],
  hops: [
    { hop_no: 1, ip: "192.168.1.1", hostname: null, asn: null, as_name: null, avg_ms: 0.4, loss_pct: 0, geo: null, note: "private address" },
    { hop_no: 2, ip: "10.0.0.1", hostname: null, asn: null, as_name: null, avg_ms: 1.2, loss_pct: 0, geo: null, note: "private address" },
    { hop_no: 3, ip: "203.0.113.1", hostname: "core1.example", asn: "AS64500", as_name: "Example Transit GmbH", avg_ms: 8, loss_pct: 0, geo: frankfurt, note: null },
    { hop_no: 4, ip: "203.0.113.2", hostname: "core2.example", asn: "AS64500", as_name: "Example Transit GmbH", avg_ms: 8.5, loss_pct: 10, geo: { ...frankfurt, lat: 50.12 }, note: null },
    { hop_no: 5, ip: "203.0.113.9", hostname: null, asn: null, as_name: null, avg_ms: 15, loss_pct: 0, geo: null, note: "not in database" },
    // A transit router that GeoLite2 registers in the target's city, two hops before the target itself.
    { hop_no: 6, ip: "203.0.113.20", hostname: "edge.example", asn: "AS64501", as_name: "Example Hosting Ltd", avg_ms: 18, loss_pct: 0, geo: london, note: null },
    // mtr reported a number the ASN database disagrees with: the number stays, no name is attached.
    { hop_no: 7, ip: "203.0.113.21", hostname: null, asn: "AS64500", as_name: null, avg_ms: 19, loss_pct: 0, geo: frankfurt, note: null },
    { hop_no: 8, ip: "192.0.2.1", hostname: "www.example", asn: "AS64501", as_name: "Example Hosting Ltd", avg_ms: 20, loss_pct: 0, geo: london, note: null },
  ],
  destination: { ip: "192.0.2.1", host: "www.example", role: "target", reached: true, geo: london, note: null, asn: "AS64501", as_name: "Example Hosting Ltd" },
};

describe("path map", () => {
  it("keeps hop order in the line, merges markers per place and formats hop ranges", () => {
    const { stops, paths, places, route, networks } = buildStops(geo);
    expect(stops.map((s) => [s.id, s.kind])).toEqual([["src-0", "source"], ["hop-3", "hop"], ["hop-6", "hop"], ["hop-7", "hop"], ["dst", "destination"]]);
    expect(stops[1].hopNos).toEqual([3, 4]);
    expect(stops[1].lines).toEqual(["3. core1.example (203.0.113.1) · AS64500 Example Transit GmbH · 8.0 ms", "4. core2.example (203.0.113.2) · AS64500 Example Transit GmbH · 8.5 ms · 10.0% loss"]);
    expect(stops[1].place).toBe("Frankfurt, Hesse, Germany");
    expect(paths).toEqual([[[52.52, 13.405], [50.11, 8.68], [51.5, -0.12], [50.11, 8.68], [51.5, -0.12]]]);
    // Three places, not five markers: Frankfurt holds hops 3, 4 and 7; London holds hop 6 and the target.
    expect(places.map((p) => [p.kind, p.label])).toEqual([["source", "Monitor"], ["hop", "3–4, 7"], ["destination", "Target · 6"]]);
    expect(places[2].title).toBe("Target · www.example · also hop 6");
    expect(places[1].lines).toHaveLength(3);
    expect(route.map((r) => `${r.place} ${r.what}`)).toEqual(["Berlin, Germany monitor", "Frankfurt, Hesse, Germany hops 3–4", "London, United Kingdom hop 6", "Frankfurt, Hesse, Germany hop 7", "London, United Kingdom hop 8, target"]);
    // Steps point at the marker they sit on, and carry only their own hops' detail lines.
    expect(route.map((r) => r.placeId)).toEqual(["place-0", "place-1", "place-2", "place-1", "place-2"]);
    expect(route[3].lines).toEqual(["7. 203.0.113.21 · AS64500 · 19.0 ms"]);
    expect(hopRanges([5, 1, 2, 3, 8])).toBe("1–3, 5, 8");

    // Networks: each stop, marker and step names the autonomous systems it holds, once each, in hop order;
    // a hop whose name is unknown contributes its bare number. The whole path lists them start to end.
    expect(stops.map((s) => s.networks)).toEqual([["AS64499 Home ISP AG"], ["AS64500 Example Transit GmbH"], ["AS64501 Example Hosting Ltd"], ["AS64500"], ["AS64501 Example Hosting Ltd"]]);
    expect(places.map((p) => p.networks)).toEqual([["AS64499 Home ISP AG"], ["AS64500 Example Transit GmbH", "AS64500"], ["AS64501 Example Hosting Ltd"]]);
    expect(route.map((r) => r.networks)).toEqual([["AS64499 Home ISP AG"], ["AS64500 Example Transit GmbH"], ["AS64501 Example Hosting Ltd"], ["AS64500"], ["AS64501 Example Hosting Ltd"]]);
    expect(networks).toEqual(["AS64499 Home ISP AG", "AS64500 Example Transit GmbH", "AS64501 Example Hosting Ltd", "AS64500"]);

    // A ping-style probe has no hops: the destination is placed on its own, one line per located source.
    // The start marker carries its evidence (how it was placed), its network and the accuracy radius.
    expect(stops[0].lines).toEqual(["placed by the public address it is seen from, 198.51.100.7, not by its own address 192.168.1.20", "network AS64499 Home ISP AG"]);
    expect(stops[0].accuracyKm).toBe(50);
    expect(places[1].accuracyKm).toBe(20);

    const direct = buildStops({ ...geo, hops: [], sources: [geo.sources[0], { kind: "probe", label: "Frankfurt, DE", ip: null, geo: frankfurt, note: null, evidence: "coordinates reported by the Globalping probe itself", asn: "AS24940", as_name: "Hetzner Online GmbH" }] });
    expect(direct.stops.map((s) => s.kind)).toEqual(["source", "source", "destination"]);
    expect(direct.paths).toHaveLength(2);
    expect(direct.places.map((p) => p.label)).toEqual(["Probe", "Probe", "Target"]);
    // Without hops the destination marker still names its network, and the probe its own.
    expect(direct.stops[1].networks).toEqual(["AS24940 Hetzner Online GmbH"]);
    expect(direct.stops[2].lines).toEqual(["192.0.2.1", "network AS64501 Example Hosting Ltd"]);
    expect(direct.networks).toEqual(["AS64499 Home ISP AG", "AS24940 Hetzner Online GmbH", "AS64501 Example Hosting Ltd"]);

    // A DNS check's far end is the resolver it asked, named as such on the marker and in the route.
    const dns = buildStops({ ...geo, hops: [], destination: { ip: "9.9.9.9", host: "9.9.9.9", role: "resolver", reached: true, geo: frankfurt, note: null, asn: null, as_name: null } });
    expect(dns.places.map((p) => p.label)).toEqual(["Monitor", "Resolver"]);
    expect(dns.route.map((r) => r.what)).toEqual(["monitor", "resolver"]);
    expect(dns.stops[1].networks).toEqual([]);
  });

  it("lets a route step focus the map and expand its hops, and a marker select its step", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><PathMapCard geo={geo} pathProbe /></MemoryRouter>);
    const map = await screen.findByTestId("path-map");
    const steps = within(screen.getByRole("list", { name: "Route by place" })).getAllByRole("button");
    expect(steps.every((b) => b.getAttribute("aria-expanded") === "false")).toBe(true);
    expect(screen.queryByRole("region")).toBeNull();

    // Selecting a step expands its hop lines and asks the map to show that place.
    await user.click(steps[1]);
    expect(steps[1].getAttribute("aria-expanded")).toBe("true");
    const details = screen.getByRole("region", { name: "Frankfurt, Hesse, Germany details" });
    expect(within(details).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["3. core1.example (203.0.113.1) · AS64500 Example Transit GmbH · 8.0 ms", "4. core2.example (203.0.113.2) · AS64500 Example Transit GmbH · 8.5 ms · 10.0% loss"]);
    expect(details.textContent).toContain("· hops 3–4 · AS64500 Example Transit GmbH · GeoLite2 accuracy about 20 km");
    expect(map.getAttribute("data-focus")).toBe("place-1");

    // Arrow keys move between steps; selecting the open step again closes it.
    steps[1].focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(steps[2]);
    await user.keyboard("{End}{ArrowRight}");
    expect(document.activeElement).toBe(steps[0]);
    await user.click(steps[1]);
    expect(steps[1].getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("region")).toBeNull();

    // A marker on the map selects the first step at that place.
    await user.click(within(map).getByRole("button", { name: "destination[Target · 6]" }));
    expect(steps[2].getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("region", { name: "London, United Kingdom details" }).textContent).toContain("6. edge.example (203.0.113.20)");
  });

  it("names an unresolvable far end instead of drawing nothing", () => {
    render(<MemoryRouter><PathMapCard geo={{ ...geo, hops: [], destination: { ip: null, host: "portal.example", role: "target", reached: false, geo: null, note: "host could not be resolved", asn: null, as_name: null } }} pathProbe={false} /></MemoryRouter>);
    expect(screen.getByText("Latest run · monitor and target · target not located")).toBeTruthy();
    expect(screen.getByText("Not on the map:").parentElement!.textContent).toContain("target portal.example · host could not be resolved");
  });

  it("shows the map, the route by place, the located-hop caption and the hops it could not place", async () => {
    render(<MemoryRouter><PathMapCard geo={geo} pathProbe /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: /Path map/ })).toBeTruthy();
    expect(screen.getByText("Latest run · 5 of 8 hops located · one marker per place, numbers are hops")).toBeTruthy();
    // The map chunk is lazy-loaded, so it appears after the suspense fallback.
    expect((await screen.findByTestId("path-map")).textContent).toBe("source[Monitor]hop[3–4, 7]destination[Target · 6]");
    const route = screen.getByRole("list", { name: "Route by place" });
    // Every step prints the networks it crosses after the hop numbers.
    expect(within(route).getAllByRole("button").map((b) => b.textContent)).toEqual(["Berlin, GermanymonitorAS64499 Home ISP AG", "Frankfurt, Hesse, Germanyhops 3–4AS64500 Example Transit GmbH", "London, United Kingdomhop 6AS64501 Example Hosting Ltd", "Frankfurt, Hesse, Germanyhop 7AS64500", "London, United Kingdomhop 8, targetAS64501 Example Hosting Ltd"]);
    // The path's networks are listed once each, start to end, under the route.
    expect(screen.getByTestId("path-networks").textContent).toBe("Networks:AS64499 Home ISP AGAS64500 Example Transit GmbHAS64501 Example Hosting LtdAS64500");
    const notes = screen.getByText("Not on the map:").parentElement!;
    expect(notes.textContent).toContain("hops 1–2 · private address");
    expect(notes.textContent).toContain("hop 5 · not in database");
    expect(screen.getByText(/city-level estimates from GeoLite2/)).toBeTruthy();
    // The evidence line says how the monitor was placed, how precise that is, and links to the lookup page.
    const evidence = screen.getByTestId("start-evidence");
    expect(evidence.textContent).toContain("Monitor position: placed by the public address it is seen from, 198.51.100.7, not by its own address 192.168.1.20 · about 50 km accuracy.");
    expect(within(evidence).getByRole("link", { name: "GeoIP lookup" }).getAttribute("href")).toBe("/geoip");
  });

  it("explains missing network names when the ASN database is not downloaded yet", async () => {
    const bare = { ...geo, asn_available: false, sources: geo.sources.map((s) => ({ ...s, asn: null, as_name: null })), hops: geo.hops.map((h) => ({ ...h, as_name: null })), destination: { ...geo.destination!, asn: null, as_name: null } };
    render(<MemoryRouter><PathMapCard geo={bare} pathProbe /></MemoryRouter>);
    await screen.findByTestId("path-map");
    // The mtr-reported numbers still show on the steps; the summary line says why there are no names.
    const route = screen.getByRole("list", { name: "Route by place" });
    expect(within(route).getAllByRole("button")[1].textContent).toBe("Frankfurt, Hesse, Germanyhops 3–4AS64500");
    const line = screen.getByTestId("path-networks");
    expect(line.textContent).toBe("Networks:AS64500AS64501· names appear once the GeoLite2 ASN database is downloaded; it is fetched with the City database (Download now under Settings).");
    expect(within(line).getByRole("link", { name: "Download now" }).getAttribute("href")).toBe("/settings");
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
    expect(screen.queryByRole("list", { name: "Route by place" })).toBeNull();
  });
});

describe("GeoIP lookup page", () => {
  it("looks up an address, shows the evidence and places it on the map", async () => {
    const user = userEvent.setup();
    const lookup = vi.spyOn(api, "geoipLookup").mockImplementation(async (q) => q === "self"
      ? { query: "self", kind: "public_ip", host: "This server (public address)", ip: "198.51.100.7", geo: berlin, note: null, asn: "AS64499", as_name: "Home ISP AG", configured: true, available: true, asn_available: true, simulated: false, build_epoch: "2026-09-01T00:00:00Z" }
      : { query: q, kind: "host", host: q, ip: "203.0.113.9", geo: { ...frankfurt, accuracy_km: 200 }, note: null, asn: "AS64500", as_name: "Example Transit GmbH", configured: true, available: true, asn_available: true, simulated: false, build_epoch: "2026-09-01T00:00:00Z" });
    render(<MemoryRouter><GeoLookup /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "Look up" }).hasAttribute("disabled")).toBe(true);
    await user.type(screen.getByLabelText("IP address or host name"), "www.example.com{Enter}");
    await waitFor(() => expect(lookup).toHaveBeenCalledWith("www.example.com"));
    const result = await screen.findByLabelText("Lookup result");
    expect(result.textContent).toContain("Host name");
    expect(result.textContent).toContain("203.0.113.9");
    expect(result.textContent).toContain("Frankfurt, Hesse, Germany");
    expect(result.textContent).toContain("about 200 km radius");
    expect(result.textContent).toContain("GeoLite2 City built 2026-09-01 and GeoLite2 ASN");
    expect(result.textContent).toContain("NetworkAS64500 Example Transit GmbH");
    expect((await screen.findByTestId("path-map")).textContent).toBe("destination[203.0.113.9]");

    await user.click(screen.getByRole("button", { name: "This server" }));
    await waitFor(() => expect(lookup).toHaveBeenCalledWith("self"));
    expect((await screen.findByLabelText("Lookup result")).textContent).toContain("This server, by the public address it is seen from");
    expect(screen.getByTestId("path-map").textContent).toBe("source[Monitor]");
  });

  it("explains a private address and a missing database", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "geoipLookup").mockResolvedValue({ query: "10.0.0.1", kind: "address", host: null, ip: "10.0.0.1", geo: null, note: "private address", asn: null, as_name: null, configured: false, available: false, asn_available: false, simulated: false, build_epoch: null });
    render(<MemoryRouter><GeoLookup /></MemoryRouter>);
    await user.type(screen.getByLabelText("IP address or host name"), "10.0.0.1{Enter}");
    const result = await screen.findByLabelText("Lookup result");
    expect(result.textContent).toContain("private address");
    expect(screen.getByText(/No MaxMind licence key is saved/)).toBeTruthy();
    expect(screen.getByText("Nothing to place on a map.")).toBeTruthy();
  });
});

const settings: SettingsT = {
  retention_days: 30, asn_lookup: true, reverse_dns: true, webhook_url: "", webhook_events: [], pushover_enabled: false,
  pushover_user_key: "", pushover_api_token: "", pushover_device: "", pushover_sound: "", pushover_priority: "auto", pushover_events: [],
  base_url: "", site_name: "MTR Tracker", tag_colors: {}, globalping_token: "", maxmind_account_id: "", maxmind_license_key: "",
};
const geoipStatus: GeoIpStatus = {
  configured: false, available: false, simulated: false, edition: "GeoLite2-City", path: "/data/geoip/GeoLite2-City.mmdb", build_epoch: null,
  downloaded_at: null, asn_available: false, asn_edition: "GeoLite2-ASN", asn_build_epoch: null, asn_downloaded_at: null,
  last_attempt: null, last_success: null, last_error: null, updating: false, refresh_after_sec: 604800,
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
    expect(within(section).getByText(/Databases not downloaded yet/)).toBeTruthy();

    // The City database alone: locations are reported and the missing ASN database is named.
    statusMock.mockResolvedValue({ ...geoipStatus, configured: true, available: true, build_epoch: "2026-09-01T00:00:00Z", downloaded_at: new Date().toISOString() });
    await user.click(downloadButton);
    await waitFor(() => expect(download).toHaveBeenCalledOnce());
    expect(await within(section).findByText(/GeoLite2-City/)).toBeTruthy();
    expect(within(section).getByText(/built 2026-09-01/)).toBeTruthy();
    expect(within(section).getByText(/not downloaded yet, so the map shows no network names/)).toBeTruthy();

    // Both databases present (after another download): each is listed with its own build date.
    statusMock.mockResolvedValue({ ...geoipStatus, configured: true, available: true, build_epoch: "2026-09-01T00:00:00Z", downloaded_at: new Date().toISOString(), asn_available: true, asn_build_epoch: "2026-09-02T00:00:00Z", asn_downloaded_at: new Date().toISOString() });
    await user.click(downloadButton);
    await waitFor(() => expect(download).toHaveBeenCalledTimes(2));
    expect(await within(section).findByText(/GeoLite2-ASN/)).toBeTruthy();
    expect(within(section).getByText(/built 2026-09-02/)).toBeTruthy();
    expect(within(section).queryByText(/no network names/)).toBeNull();
  });
});
