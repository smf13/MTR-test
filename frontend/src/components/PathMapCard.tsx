import { lazy, Suspense, useMemo } from "react";
import { Link } from "react-router-dom";
import { Map as MapIcon, MapPin } from "lucide-react";
import type { GeoHop, GeoPoint, PathGeo } from "../api";
import { useDocumentTheme } from "../hooks";
import { fmtNum } from "../utils";
import { HelpTip } from "./Popover";

// Leaflet (and its stylesheet) load only when a map is actually shown.
const PathMap = lazy(() => import("./PathMap"));

/** One marker on the map: a source, a group of consecutive hops in the same place, or the destination. */
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

export function placeOf(geo: GeoPoint | null): string {
  if (!geo) return "";
  return [geo.city, geo.region && geo.region !== geo.city ? geo.region : null, geo.country].filter(Boolean).join(", ");
}

function hopLine(h: GeoHop): string {
  const who = h.hostname && h.ip ? `${h.hostname} (${h.ip})` : h.hostname || h.ip || "no response";
  const stats = [h.asn, h.avg_ms !== null ? `${fmtNum(h.avg_ms)} ms` : null, h.loss_pct ? `${fmtNum(h.loss_pct)}% loss` : null].filter(Boolean).join(" · ");
  return `${h.hop_no}. ${who}${stats ? ` · ${stats}` : ""}`;
}

function near(a: { lat: number; lon: number }, b: GeoPoint): boolean {
  return Math.abs(a.lat - b.lat) < 0.05 && Math.abs(a.lon - b.lon) < 0.05;
}

/** Markers and the lines between them, in path order; consecutive hops at one location share a marker. */
export function buildStops(geo: PathGeo): { stops: MapStop[]; paths: [number, number][][] } {
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
      title: isDest ? `Destination · ${geo.destination?.host ?? h.ip}` : `Hop ${h.hop_no}`,
      place: placeOf(h.geo),
      lines: [hopLine(h)],
      hopNos: [h.hop_no],
      reached: isDest ? geo.destination?.reached : undefined,
    });
  }
  if (geo.destination?.geo && !chain.some((s) => s.kind === "destination")) {
    const d = geo.destination;
    chain.push({ id: "dst", kind: "destination", lat: d.geo!.lat, lon: d.geo!.lon, title: `Destination · ${d.host}`, place: placeOf(d.geo), lines: [d.ip], hopNos: [], reached: d.reached });
  }
  const line = chain.map((s) => [s.lat, s.lon] as [number, number]);
  const paths = sources.length ? sources.map((s) => [[s.lat, s.lon] as [number, number], ...line]) : [line];
  return { stops: [...sources, ...chain], paths: paths.filter((p) => p.length > 1) };
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
        <span>Map: add a MaxMind licence key under <Link to="/settings" className="text-accent hover:underline">Settings</Link> to plot {pathProbe ? "the monitor, every hop and the destination" : "the monitor and the destination"} on a map.</span>
      </div>
    );
  }
  const located = geo.hops.filter((h) => h.geo).length;
  const sourcesLocated = geo.sources.filter((s) => s.geo).length;
  return (
    <div className="card p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="chart-heading"><MapIcon size={15} /> {pathProbe ? "Path map" : "Location map"}<HelpTip label="path map">Locations come from the MaxMind GeoLite2 City database and are accurate to about a city. The monitor is placed by its own address, or by the public address it is seen from when it sits behind NAT. Private hops and addresses missing from the database are listed below the map. A Globalping probe is placed where the probe reports itself.</HelpTip></h2>
          <p className="chart-caption">
            {geo.run_id ? `Latest run${pathProbe ? ` · ${located} of ${geo.hops.length} hops located` : ""}${sourcesLocated ? "" : " · monitor not located"}` : "Waiting for the first run…"}
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-faint">
          <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-full" style={{ background: "var(--accent)" }} /> monitor</span>
          {pathProbe && <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "var(--paused)" }} /> hop</span>}
          <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-full" style={{ background: "var(--up)" }} /> destination</span>
        </div>
      </div>
      {!geo.available ? (
        <div className="flex items-center justify-center rounded-lg border border-dashed border-border-strong px-4 py-10 text-center text-sm text-faint" style={{ minHeight: 160 }}>
          The GeoLite2 database has not been downloaded yet. It is fetched in the background after the key is saved; <Link to="/settings" className="ml-1 text-accent hover:underline">Download now</Link> under Settings fetches it immediately.
        </div>
      ) : built && built.stops.length ? (
        <Suspense fallback={<div className="flex items-center justify-center text-sm text-faint" style={{ height: 360 }}>Loading map…</div>}>
          <PathMap stops={built.stops} paths={built.paths} dark={theme !== "light"} />
        </Suspense>
      ) : (
        <div className="flex items-center justify-center text-sm text-faint" style={{ height: 160 }}>{geo.run_id ? "Nothing on this path could be located." : "No completed run yet."}</div>
      )}
      {unlocated.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-faint">
          <span>Not located:</span>
          {unlocated.map(([note, nos]) => (
            <span key={note}><span className="font-mono text-muted">{nos.length === 1 ? `hop ${nos[0]}` : `hops ${hopRanges(nos)}`}</span> · {note}</span>
          ))}
        </div>
      )}
    </div>
  );
}
