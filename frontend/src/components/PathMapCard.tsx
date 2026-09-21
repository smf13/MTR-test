import { lazy, Suspense, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, Map as MapIcon, MapPin, Network } from "lucide-react";
import type { GeoHop, GeoPoint, PathGeo } from "../api";
import { useDocumentTheme } from "../hooks";
import { fmtNum } from "../utils";
import { HelpTip } from "./Popover";

// Leaflet (and its stylesheet) load only when a map is actually shown.
const PathMap = lazy(() => import("./PathMap"));

/** One step of the path in order: a source, a run of consecutive hops in one place, or the destination. */
export interface MapStop {
  id: string;
  kind: "source" | "hop" | "destination";
  lat: number;
  lon: number;
  title: string;
  place: string;
  /** Extra popup lines: host names, addresses, latency. */
  lines: string[];
  hopNos: number[];
  reached?: boolean;
  /** Accuracy radius in km for this position, when the provider gives one (GeoLite2 does, ip-api.com does not). */
  accuracyKm?: number | null;
  /** Networks at this stop in hop order ("AS64500 Example Transit GmbH"), without repeats. */
  networks: string[];
}

/** One marker on the map: every stop that lands in the same place, whether or not they are consecutive. */
export interface MapPlace {
  id: string;
  kind: "source" | "hop" | "destination";
  lat: number;
  lon: number;
  /** Short text printed on the marker: "Monitor", "Target", "3–5", "Target · 7, 8". */
  label: string;
  title: string;
  place: string;
  lines: string[];
  hopNos: number[];
  reached?: boolean;
  accuracyKm?: number | null;
  /** Networks of every stop at this place, in path order, without repeats. */
  networks: string[];
}

/** "about 100 km" for an accuracy radius; empty when unknown. */
export function accuracyText(km: number | null | undefined): string {
  return km === null || km === undefined ? "" : `about ${km} km`;
}

/** One entry of the textual route under the map: consecutive stops in one place. */
export interface RouteStep {
  place: string;
  what: string;
  kind: "source" | "hop" | "destination";
  /** The marker this step sits on, so selecting the step can focus the map and vice versa. */
  placeId: string;
  /** Detail lines shown when the step is expanded (same content as the marker popup, for these stops only). */
  lines: string[];
  /** Networks crossed in this step, in hop order, without repeats; printed on the step itself. */
  networks: string[];
}

/** A request for the map to pan to a place and open its popup; `seq` makes repeated selections of one place distinct. */
export interface MapFocus {
  id: string;
  seq: number;
}

export function placeOf(geo: GeoPoint | null): string {
  if (!geo) return "";
  return [geo.city, geo.region && geo.region !== geo.city ? geo.region : null, geo.country].filter(Boolean).join(", ");
}

/** "AS64500 Example Transit GmbH", or whichever half is known; empty when neither is. */
export function networkText(asn: string | null | undefined, name: string | null | undefined): string {
  return [asn, name].filter(Boolean).join(" ");
}

function hopLine(h: GeoHop): string {
  const who = h.hostname && h.ip ? `${h.hostname} (${h.ip})` : h.hostname || h.ip || "no response";
  const stats = [networkText(h.asn, h.as_name), h.avg_ms !== null ? `${fmtNum(h.avg_ms)} ms` : null, h.loss_pct ? `${fmtNum(h.loss_pct)}% loss` : null].filter(Boolean).join(" · ");
  return `${h.hop_no}. ${who}${stats ? ` · ${stats}` : ""}`;
}

/** Append a network to a list in path order, skipping repeats and unknowns. */
function addNetwork(list: string[], text: string): void {
  if (text && !list.includes(text)) list.push(text);
}

function near(a: { lat: number; lon: number }, b: { lat: number; lon: number }): boolean {
  return Math.abs(a.lat - b.lat) < 0.05 && Math.abs(a.lon - b.lon) < 0.05;
}

/** "1–3, 5" for a list of hop numbers. */
export function hopRanges(nos: number[]): string {
  const sorted = [...nos].sort((a, b) => a - b);
  const parts: string[] = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (const n of sorted.slice(1)) {
    if (n === prev + 1) {
      prev = n;
      continue;
    }
    parts.push(start === prev ? `${start}` : `${start}–${prev}`);
    start = prev = n;
  }
  if (sorted.length) parts.push(start === prev ? `${start}` : `${start}–${prev}`);
  return parts.join(", ");
}

function hopsWord(nos: number[]): string {
  return `${nos.length === 1 ? "hop" : "hops"} ${hopRanges(nos)}`;
}

/** Stops in path order, the lines between them, one marker per place and the textual route. */
export function buildStops(geo: PathGeo): { stops: MapStop[]; paths: [number, number][][]; places: MapPlace[]; route: RouteStep[]; networks: string[] } {
  const sources: MapStop[] = geo.sources
    .filter((s) => s.geo)
    .map((s, i) => {
      const net = networkText(s.asn, s.as_name);
      return { id: `src-${i}`, kind: "source" as const, lat: s.geo!.lat, lon: s.geo!.lon, title: s.label, place: placeOf(s.geo), lines: [s.evidence, ...(s.ip && !s.evidence.includes(s.ip) ? [s.ip] : []), ...(net ? [`network ${net}`] : [])].filter(Boolean), hopNos: [], accuracyKm: s.geo!.accuracy_km, networks: net ? [net] : [] };
    });
  const destIp = geo.destination?.ip ?? null;
  const destWord = destinationWord(geo.destination?.role);
  const chain: MapStop[] = [];
  for (const h of geo.hops) {
    if (!h.geo) continue;
    const isDest = !!destIp && h.ip === destIp;
    const last = chain[chain.length - 1];
    const net = networkText(h.asn, h.as_name);
    if (last && !isDest && last.kind === "hop" && near(last, h.geo)) {
      last.hopNos.push(h.hop_no);
      last.lines.push(hopLine(h));
      addNetwork(last.networks, net);
      continue;
    }
    chain.push({
      id: isDest ? "dst" : `hop-${h.hop_no}`,
      kind: isDest ? "destination" : "hop",
      lat: h.geo.lat,
      lon: h.geo.lon,
      title: isDest ? `${destWord} · ${geo.destination?.host ?? h.ip}` : `Hop ${h.hop_no}`,
      place: placeOf(h.geo),
      lines: [hopLine(h)],
      hopNos: [h.hop_no],
      reached: isDest ? geo.destination?.reached : undefined,
      accuracyKm: h.geo.accuracy_km,
      networks: net ? [net] : [],
    });
  }
  if (geo.destination?.geo && !chain.some((s) => s.kind === "destination")) {
    const d = geo.destination;
    const net = networkText(d.asn, d.as_name);
    chain.push({ id: "dst", kind: "destination", lat: d.geo!.lat, lon: d.geo!.lon, title: `${destWord} · ${d.host}`, place: placeOf(d.geo), lines: [...(d.ip && d.ip !== d.host ? [d.ip] : []), ...(net ? [`network ${net}`] : [])], hopNos: [], reached: d.reached, accuracyKm: d.geo!.accuracy_km, networks: net ? [net] : [] });
  }
  const line = chain.map((s) => [s.lat, s.lon] as [number, number]);
  const paths = sources.length ? sources.map((s) => [[s.lat, s.lon] as [number, number], ...line]) : [line];
  const stops = [...sources, ...chain];

  // Markers: GeoLite2 places many transit routers in the same city as their operator or the target, so
  // non-consecutive stops often share coordinates. One marker per place, listing every hop there, keeps a
  // hop from hiding under the target marker.
  const places: MapPlace[] = [];
  const members = new Map<string, MapStop[]>();
  for (const stop of stops) {
    let p = places.find((x) => near(x, stop));
    if (!p) {
      p = { id: `place-${places.length}`, kind: "hop", lat: stop.lat, lon: stop.lon, label: "", title: "", place: stop.place, lines: [], hopNos: [], networks: [] };
      places.push(p);
      members.set(p.id, []);
    }
    members.get(p.id)!.push(stop);
  }
  for (const p of places) {
    const group = members.get(p.id)!;
    const src = group.filter((s) => s.kind === "source");
    const dst = group.find((s) => s.kind === "destination");
    const hopNos = group.flatMap((s) => s.hopNos);
    p.hopNos = hopNos;
    p.lines = group.flatMap((s) => s.lines);
    group.forEach((s) => s.networks.forEach((n) => addNetwork(p.networks, n)));
    p.reached = dst?.reached;
    p.accuracyKm = group.map((s) => s.accuracyKm).find((a) => a !== null && a !== undefined) ?? null;
    p.kind = dst ? "destination" : src.length ? "source" : "hop";
    const hopText = hopNos.length ? hopRanges(hopNos) : "";
    if (dst) {
      const extra = hopNos.filter((n) => !dst.hopNos.includes(n));
      p.label = extra.length ? `${destWord} · ${hopRanges(extra)}` : destWord;
      p.title = extra.length ? `${dst.title} · also ${hopsWord(extra)}` : dst.title;
    } else if (src.length) {
      const name = src.length > 1 ? `${src.length} probes` : geo.sources.some((s) => s.kind === "probe") ? "Probe" : "Monitor";
      p.label = hopText ? `${name} · ${hopText}` : name;
      p.title = hopText ? `${src.map((s) => s.title).join(", ")} · also ${hopsWord(hopNos)}` : src.map((s) => s.title).join(", ");
    } else {
      p.label = hopText;
      p.title = hopsWord(hopNos).replace(/^h/, "H");
    }
  }

  // The textual route: consecutive stops in one place become one step ("Frankfurt · hops 3–5").
  const remote = geo.sources.some((s) => s.kind === "probe");
  const steps: { stop: MapStop; hopNos: number[]; lines: string[]; networks: string[] }[] = [];
  for (const stop of stops) {
    const prev = steps[steps.length - 1];
    if (prev && prev.stop.kind === "hop" && stop.kind === "hop" && (near(prev.stop, stop) || (stop.place && prev.stop.place === stop.place))) {
      prev.hopNos.push(...stop.hopNos);
      prev.lines.push(...stop.lines);
      stop.networks.forEach((n) => addNetwork(prev.networks, n));
      continue;
    }
    steps.push({ stop, hopNos: [...stop.hopNos], lines: [...stop.lines], networks: [...stop.networks] });
  }
  const route: RouteStep[] = steps.map(({ stop, hopNos, lines, networks }) => ({
    place: stop.place || "unknown place",
    kind: stop.kind,
    what: stop.kind === "source" ? (remote ? "probe" : "monitor") : stop.kind === "destination" ? (hopNos.length ? `hop ${hopNos[0]}, ${destWord.toLowerCase()}` : destWord.toLowerCase()) : hopsWord(hopNos),
    placeId: places.find((p) => near(p, stop))?.id ?? "",
    lines,
    networks,
  }));
  // The networks the path crosses, start to end, each once at its first appearance.
  const networks: string[] = [];
  stops.forEach((s) => s.networks.forEach((n) => addNetwork(networks, n)));
  return { stops, paths: paths.filter((p) => p.length > 1), places, route, networks };
}

/** Marker word for the far end: the target itself, or for DNS checks the resolver asked or the answer's address. */
export function destinationWord(role: "target" | "resolver" | "answer" | undefined): string {
  return role === "resolver" ? "Resolver" : role === "answer" ? "Answer" : "Target";
}

/** The map card of a target page: shown once ip-api.com is on or a MaxMind licence key is saved, otherwise a one-line pointer to Settings. */
export function PathMapCard({ geo, pathProbe }: { geo: PathGeo | null; pathProbe: boolean }) {
  const theme = useDocumentTheme();
  const built = useMemo(() => (geo && geo.enabled ? buildStops(geo) : null), [geo]);
  // The expanded route step and the place the map should show; a marker click selects the first step at that place.
  const [openStep, setOpenStep] = useState<number | null>(null);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const stepRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const routeKey = built ? built.route.map((s) => `${s.placeId}:${s.what}`).join("|") : "";
  useEffect(() => {
    // A different run (new places or hops) invalidates the selection; a plain poll refresh keeps it.
    setOpenStep(null);
    setFocus(null);
  }, [routeKey]);
  const selectStep = (i: number) => {
    const step = built?.route[i];
    if (!step) return;
    const willOpen = openStep !== i;
    setOpenStep(willOpen ? i : null);
    if (willOpen && step.placeId) setFocus((f) => ({ id: step.placeId, seq: (f?.seq ?? 0) + 1 }));
  };
  const selectPlace = (placeId: string) => {
    const i = built?.route.findIndex((s) => s.placeId === placeId) ?? -1;
    if (i >= 0) setOpenStep(i);
  };
  const onStepKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const n = built?.route.length ?? 0;
    if (!n || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const next = e.key === "Home" ? 0 : e.key === "End" ? n - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + n) % n;
    stepRefs.current[next]?.focus();
  };
  const unlocated = useMemo(() => {
    const groups = new Map<string, number[]>();
    geo?.hops.forEach((h) => {
      if (h.geo) return;
      const note = h.note ?? "not located";
      groups.set(note, [...(groups.get(note) ?? []), h.hop_no]);
    });
    return [...groups.entries()];
  }, [geo]);
  if (!geo) return null;
  if (!geo.enabled) {
    return (
      <div className="card flex flex-wrap items-center gap-2 px-4 py-2.5 text-xs text-muted">
        <MapPin size={13} />
        <span>Map: switch on ip-api.com or add a MaxMind licence key under <Link to="/settings" className="text-accent hover:underline">Settings</Link> to plot {pathProbe ? "the monitor, every hop and the target" : "the monitor and the target"} on a map.</span>
      </div>
    );
  }
  const located = geo.hops.filter((h) => h.geo).length;
  const sourcesLocated = geo.sources.filter((s) => s.geo).length;
  const remote = geo.sources.some((s) => s.kind === "probe");
  const destWord = destinationWord(geo.destination?.role);
  const destMissing = geo.run_id && (!geo.destination || !geo.destination.geo) ? (geo.destination?.note ?? "no address recorded") : null;
  // The evidence for the start marker: how the position was obtained and how precise the provider says it is.
  const start = geo.sources[0];
  const startEvidence = start ? `${start.evidence}${start.geo?.accuracy_km !== null && start.geo?.accuracy_km !== undefined ? ` · ${accuracyText(start.geo.accuracy_km)} accuracy` : ""}${start.note ? ` · ${start.note}` : ""}` : "";
  return (
    <div className="card p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="chart-heading"><MapIcon size={15} /> {pathProbe ? "Path map" : "Location map"}<HelpTip label="path map">
            <p>Every address of the latest run is placed with the GeoIP provider set up under Settings: {geo.ip_api_enabled ? "ip-api.com, asked in one batched request per run, with the MaxMind GeoLite2 City database answering whatever it cannot" : "the MaxMind GeoLite2 City database"}. The dashed line follows the hops in order, from the {remote ? "Globalping probe" : "monitor"} to the target.</p>
            <p className="mt-2">A marker stands for a place, not a hop: the numbers on it are the hops located there, so "Target · 7, 8" means hops 7 and 8 were placed in the target's city. GeoIP data knows a city at best, and it often registers backbone routers at their operator's head office, so a path can appear to double back or to reach the target's city several hops early. That is the provider's estimate, not a routing fault.</p>
            <p className="mt-2">Each step and marker also names the networks it crosses: the autonomous system number and the organisation behind it, from {geo.ip_api_enabled ? "ip-api.com or " : ""}the GeoLite2 ASN database, downloaded with the City database. The number mtr reported for a hop is kept; the provider only adds the name (or the number when mtr reported none). The "Networks" line lists them in path order.</p>
            <p className="mt-2">The {remote ? "probe is placed where it reports itself" : "monitor is placed by its own address, or by the public address it is seen from when it sits behind NAT"}. The far end is the address the run talked to: for HTTP the URL's host, for a DNS check the resolver it asked, or, with no resolver configured, the address in the answer. Hops with private addresses and addresses missing from the database are listed under the map instead of being drawn.</p>
          </HelpTip></h2>
          <p className="chart-caption">
            {geo.run_id ? `Latest run${pathProbe ? ` · ${located} of ${geo.hops.length} hops located · one marker per place, numbers are hops` : ` · ${remote ? "probe" : "monitor"} and ${destWord.toLowerCase()}`}${sourcesLocated ? "" : ` · ${remote ? "probe" : "monitor"} not located`}${destMissing ? ` · ${destWord.toLowerCase()} not located` : ""}` : "Waiting for the first run…"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-faint">
          <span className="inline-flex items-center gap-1"><span className="path-map-key" style={{ background: "var(--accent)" }}>{remote ? "Probe" : "Monitor"}</span> start</span>
          {pathProbe && <span className="inline-flex items-center gap-1"><span className="path-map-key" style={{ background: "var(--paused)" }}>3–5</span> hops at one place</span>}
          <span className="inline-flex items-center gap-1"><span className="path-map-key" style={{ background: "var(--up)" }}>{destWord}</span> {geo.destination?.role === "resolver" ? "resolver asked" : geo.destination?.role === "answer" ? "address in the answer" : "destination"} (red when the check failed)</span>
          <span className="inline-flex items-center gap-1"><span className="inline-block w-5 border-t-2 border-dashed" style={{ borderColor: "var(--accent)" }} /> hop order</span>
        </div>
      </div>
      {!geo.available ? (
        <div className="flex items-center justify-center rounded-lg border border-dashed border-border-strong px-4 py-10 text-center text-sm text-faint" style={{ minHeight: 160 }}>
          {geo.ip_api_enabled ? "ip-api.com is paused after a failed request and no GeoLite2 database is on disk to fall back to. Lookups resume by themselves; a MaxMind licence key under" : "The GeoLite2 database has not been downloaded yet. It is fetched in the background after the key is saved;"} <Link to="/settings" className="ml-1 text-accent hover:underline">{geo.ip_api_enabled ? "Settings" : "Download now"}</Link> {geo.ip_api_enabled ? "adds the fallback." : "under Settings fetches it immediately."}
        </div>
      ) : built && built.stops.length ? (
        <Suspense fallback={<div className="flex items-center justify-center text-sm text-faint" style={{ height: 360 }}>Loading map…</div>}>
          <PathMap places={built.places} paths={built.paths} dark={theme !== "light"} focus={focus} onSelect={selectPlace} />
        </Suspense>
      ) : (
        <div className="flex items-center justify-center text-sm text-faint" style={{ height: 160 }}>{geo.run_id ? "Nothing on this path could be located." : "No completed run yet."}</div>
      )}
      {built && built.route.length > 0 && geo.available && (
        <div className="mt-2">
          <ol className="flex flex-wrap items-center gap-x-1 gap-y-1 text-xs" aria-label="Route by place">
            {built.route.map((step, i) => (
              <li key={i} className="inline-flex items-center gap-1">
                {i > 0 && <ChevronRight size={12} className="text-faint" aria-label="then" />}
                <button
                  type="button"
                  ref={(el) => { stepRefs.current[i] = el; }}
                  className="route-step"
                  data-active={openStep === i}
                  aria-expanded={openStep === i}
                  aria-controls={openStep === i ? "route-step-details" : undefined}
                  title="Show this place on the map and list its hops"
                  onClick={() => selectStep(i)}
                  onKeyDown={(e) => onStepKey(e, i)}
                >
                  <span className="font-medium">{step.place}</span>
                  <span className="text-faint">{step.what}</span>
                  {step.networks.length > 0 && <span className="route-step-network text-faint" title={step.networks.join(", ")}>{step.networks.join(" · ")}</span>}
                  <ChevronDown size={11} className="route-step-caret text-faint" />
                </button>
              </li>
            ))}
          </ol>
          {openStep !== null && built.route[openStep] && (
            <div id="route-step-details" className="mt-1.5 rounded-lg border border-border px-3 py-2 text-xs" style={{ background: "var(--surface-2)" }} role="region" aria-label={`${built.route[openStep].place} details`}>
              <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-semibold">{built.route[openStep].place} <span className="font-normal text-faint">· {built.route[openStep].what}{built.route[openStep].networks.length ? ` · ${built.route[openStep].networks.join(", ")}` : ""}{built.places.find((p) => p.id === built.route[openStep!].placeId)?.accuracyKm != null ? ` · GeoLite2 accuracy ${accuracyText(built.places.find((p) => p.id === built.route[openStep!].placeId)!.accuracyKm)}` : ""}</span></span>
                <span className="text-faint">Shown on the map · select again to close</span>
              </div>
              {built.route[openStep].lines.length ? (
                <ul className="space-y-0.5 font-mono text-[11px] text-muted">
                  {built.route[openStep].lines.map((l) => <li key={l}>{l}</li>)}
                </ul>
              ) : (
                <div className="text-faint">No address details for this step.</div>
              )}
            </div>
          )}
        </div>
      )}
      {geo.available && geo.run_id && built && (built.networks.length > 0 || !geo.asn_available) && (
        <p className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-faint" data-testid="path-networks">
          <Network size={12} aria-hidden="true" /><span>Networks:</span>
          {built.networks.map((n, i) => (
            <span key={n} className="inline-flex items-center gap-1.5">
              {i > 0 && <ChevronRight size={12} className="text-faint" aria-label="then" />}
              <span className="font-mono text-muted">{n}</span>
            </span>
          ))}
          {!geo.asn_available && (
            <span>{built.networks.length ? "· " : ""}{geo.ip_api_enabled ? <>names appear once ip-api.com answers again or the GeoLite2 ASN database is downloaded (<Link to="/settings" className="text-accent hover:underline">Settings</Link>).</> : <>names appear once the GeoLite2 ASN database is downloaded; it is fetched with the City database (<Link to="/settings" className="text-accent hover:underline">Download now</Link> under Settings).</>}</span>
          )}
        </p>
      )}
      {(unlocated.length > 0 || destMissing) && geo.available && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-faint">
          <span>Not on the map:</span>
          {unlocated.map(([note, nos]) => (
            <span key={note}><span className="font-mono text-muted">{hopsWord(nos)}</span> · {note}</span>
          ))}
          {destMissing && <span><span className="font-mono text-muted">{destWord.toLowerCase()}{geo.destination?.host ? ` ${geo.destination.host}` : ""}</span> · {destMissing}</span>}
        </div>
      )}
      {geo.available && geo.run_id && startEvidence && (
        <p className="mt-2 text-[11px] leading-relaxed text-faint" data-testid="start-evidence">
          <span className="font-medium text-muted">{remote ? "Probe" : "Monitor"} position:</span> {startEvidence}. Check any address on the <Link to="/geoip" className="text-accent hover:underline">GeoIP lookup</Link> page.
        </p>
      )}
      {geo.available && pathProbe && geo.run_id && (
        <p className="mt-1 text-[11px] leading-relaxed text-faint">
          Positions are city-level estimates from {geo.ip_api_enabled ? "ip-api.com and GeoLite2" : "GeoLite2"}. Transit routers are often placed at their operator's registered location, so the line may double back or touch the target's city before the last hop. Select a marker, or a step in the route above, to see exactly which hops it holds and how precise the estimate is.
        </p>
      )}
    </div>
  );
}
