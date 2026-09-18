import { lazy, Suspense, useMemo } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Map as MapIcon, MapPin } from "lucide-react";
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
}

/** One entry of the textual route under the map: consecutive stops in one place. */
export interface RouteStep {
  place: string;
  what: string;
  kind: "source" | "hop" | "destination";
}

export function placeOf(geo: GeoPoint | null): string {
  if (!geo) return "";
  return [geo.city, geo.region && geo.region !== geo.city ? geo.region : null, geo.country].filter(Boolean).join(", ");
}

function hopLine(h: GeoHop): string {
  const who = h.hostname && h.ip ? `${h.hostname} (${h.ip})` : h.hostname || h.ip || "no response";
  const stats = [h.asn, h.avg_ms !== null ? `${fmtNum(h.avg_ms)} ms` : null, h.loss_pct ? `${fmtNum(h.loss_pct)}% loss` : null].filter(Boolean).join(" · ");
  return `${h.hop_no}. ${who}${stats ? ` · ${stats}` : ""}`;
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
export function buildStops(geo: PathGeo): { stops: MapStop[]; paths: [number, number][][]; places: MapPlace[]; route: RouteStep[] } {
  const sources: MapStop[] = geo.sources
    .filter((s) => s.geo)
    .map((s, i) => ({ id: `src-${i}`, kind: "source" as const, lat: s.geo!.lat, lon: s.geo!.lon, title: s.label, place: placeOf(s.geo), lines: s.ip ? [s.ip] : [], hopNos: [] }));
  const destIp = geo.destination?.ip ?? null;
  const chain: MapStop[] = [];
  for (const h of geo.hops) {
    if (!h.geo) continue;
    const isDest = !!destIp && h.ip === destIp;
    const last = chain[chain.length - 1];
    if (last && !isDest && last.kind === "hop" && near(last, h.geo)) {
      last.hopNos.push(h.hop_no);
      last.lines.push(hopLine(h));
      continue;
    }
    chain.push({
      id: isDest ? "dst" : `hop-${h.hop_no}`,
      kind: isDest ? "destination" : "hop",
      lat: h.geo.lat,
      lon: h.geo.lon,
      title: isDest ? `Target · ${geo.destination?.host ?? h.ip}` : `Hop ${h.hop_no}`,
      place: placeOf(h.geo),
      lines: [hopLine(h)],
      hopNos: [h.hop_no],
      reached: isDest ? geo.destination?.reached : undefined,
    });
  }
  if (geo.destination?.geo && !chain.some((s) => s.kind === "destination")) {
    const d = geo.destination;
    chain.push({ id: "dst", kind: "destination", lat: d.geo!.lat, lon: d.geo!.lon, title: `Target · ${d.host}`, place: placeOf(d.geo), lines: [d.ip], hopNos: [], reached: d.reached });
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
      p = { id: `place-${places.length}`, kind: "hop", lat: stop.lat, lon: stop.lon, label: "", title: "", place: stop.place, lines: [], hopNos: [] };
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
    p.reached = dst?.reached;
    p.kind = dst ? "destination" : src.length ? "source" : "hop";
    const hopText = hopNos.length ? hopRanges(hopNos) : "";
    if (dst) {
      const extra = hopNos.filter((n) => !dst.hopNos.includes(n));
      p.label = extra.length ? `Target · ${hopRanges(extra)}` : "Target";
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
  const steps: { stop: MapStop; hopNos: number[] }[] = [];
  for (const stop of stops) {
    const prev = steps[steps.length - 1];
    if (prev && prev.stop.kind === "hop" && stop.kind === "hop" && (near(prev.stop, stop) || (stop.place && prev.stop.place === stop.place))) {
      prev.hopNos.push(...stop.hopNos);
      continue;
    }
    steps.push({ stop, hopNos: [...stop.hopNos] });
  }
  const route: RouteStep[] = steps.map(({ stop, hopNos }) => ({
    place: stop.place || "unknown place",
    kind: stop.kind,
    what: stop.kind === "source" ? (remote ? "probe" : "monitor") : stop.kind === "destination" ? (hopNos.length ? `hop ${hopNos[0]}, target` : "target") : hopsWord(hopNos),
  }));
  return { stops, paths: paths.filter((p) => p.length > 1), places, route };
}

/** The map card of a target page: shown once a MaxMind licence key is saved, otherwise a one-line pointer to Settings. */
export function PathMapCard({ geo, pathProbe }: { geo: PathGeo | null; pathProbe: boolean }) {
  const theme = useDocumentTheme();
  const built = useMemo(() => (geo && geo.enabled ? buildStops(geo) : null), [geo]);
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
        <span>Map: add a MaxMind licence key under <Link to="/settings" className="text-accent hover:underline">Settings</Link> to plot {pathProbe ? "the monitor, every hop and the target" : "the monitor and the target"} on a map.</span>
      </div>
    );
  }
  const located = geo.hops.filter((h) => h.geo).length;
  const sourcesLocated = geo.sources.filter((s) => s.geo).length;
  const remote = geo.sources.some((s) => s.kind === "probe");
  return (
    <div className="card p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="chart-heading"><MapIcon size={15} /> {pathProbe ? "Path map" : "Location map"}<HelpTip label="path map">
            <p>Every address of the latest run is placed with the MaxMind GeoLite2 City database. The dashed line follows the hops in order, from the {remote ? "Globalping probe" : "monitor"} to the target.</p>
            <p className="mt-2">A marker stands for a place, not a hop: the numbers on it are the hops located there, so "Target · 7, 8" means hops 7 and 8 were placed in the target's city. GeoLite2 knows a city at best, and it often registers backbone routers at their operator's head office, so a path can appear to double back or to reach the target's city several hops early. That is the database's estimate, not a routing fault.</p>
            <p className="mt-2">The {remote ? "probe is placed where it reports itself" : "monitor is placed by its own address, or by the public address it is seen from when it sits behind NAT"}. Hops with private addresses and addresses missing from the database are listed under the map instead of being drawn.</p>
          </HelpTip></h2>
          <p className="chart-caption">
            {geo.run_id ? `Latest run${pathProbe ? ` · ${located} of ${geo.hops.length} hops located · one marker per place, numbers are hops` : ""}${sourcesLocated ? "" : ` · ${remote ? "probe" : "monitor"} not located`}` : "Waiting for the first run…"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-faint">
          <span className="inline-flex items-center gap-1"><span className="path-map-key" style={{ background: "var(--accent)" }}>{remote ? "Probe" : "Monitor"}</span> start</span>
          {pathProbe && <span className="inline-flex items-center gap-1"><span className="path-map-key" style={{ background: "var(--paused)" }}>3–5</span> hops at one place</span>}
          <span className="inline-flex items-center gap-1"><span className="path-map-key" style={{ background: "var(--up)" }}>Target</span> destination (red when unreachable)</span>
          <span className="inline-flex items-center gap-1"><span className="inline-block w-5 border-t-2 border-dashed" style={{ borderColor: "var(--accent)" }} /> hop order</span>
        </div>
      </div>
      {!geo.available ? (
        <div className="flex items-center justify-center rounded-lg border border-dashed border-border-strong px-4 py-10 text-center text-sm text-faint" style={{ minHeight: 160 }}>
          The GeoLite2 database has not been downloaded yet. It is fetched in the background after the key is saved; <Link to="/settings" className="ml-1 text-accent hover:underline">Download now</Link> under Settings fetches it immediately.
        </div>
      ) : built && built.stops.length ? (
        <Suspense fallback={<div className="flex items-center justify-center text-sm text-faint" style={{ height: 360 }}>Loading map…</div>}>
          <PathMap places={built.places} paths={built.paths} dark={theme !== "light"} />
        </Suspense>
      ) : (
        <div className="flex items-center justify-center text-sm text-faint" style={{ height: 160 }}>{geo.run_id ? "Nothing on this path could be located." : "No completed run yet."}</div>
      )}
      {built && built.route.length > 0 && geo.available && (
        <ol className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1 text-xs" aria-label="Route by place">
          {built.route.map((step, i) => (
            <li key={i} className="inline-flex items-center gap-1">
              {i > 0 && <ChevronRight size={12} className="text-faint" aria-label="then" />}
              <span className="inline-flex items-baseline gap-1 rounded-md px-1.5 py-0.5" style={{ background: "var(--surface-2)" }}>
                <span className="font-medium">{step.place}</span>
                <span className="text-faint">{step.what}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {unlocated.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-faint">
          <span>Not on the map:</span>
          {unlocated.map(([note, nos]) => (
            <span key={note}><span className="font-mono text-muted">{hopsWord(nos)}</span> · {note}</span>
          ))}
        </div>
      )}
      {geo.available && pathProbe && geo.run_id && (
        <p className="mt-2 text-[11px] leading-relaxed text-faint">
          Positions are city-level estimates from GeoLite2. Transit routers are often placed at their operator's registered location, so the line may double back or touch the target's city before the last hop; select a marker to see exactly which hops it holds.
        </p>
      )}
    </div>
  );
}
