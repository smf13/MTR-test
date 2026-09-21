import { lazy, Suspense, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { MapPin, Search, Server } from "lucide-react";
import { api, GEO_PROVIDER_LABEL, type GeoLookup as GeoLookupT, type GeoProvider } from "../api";
import { useDocumentTheme } from "../hooks";
import { ErrorBanner } from "../components/EmptyState";
import { accuracyText, networkText, placeOf, type MapPlace } from "../components/PathMapCard";

const PathMap = lazy(() => import("../components/PathMap"));

const KIND_LABEL: Record<GeoLookupT["kind"], string> = {
  address: "Address",
  host: "Host name",
  monitor: "This server, by its own address",
  public_ip: "This server, by the public address it is seen from",
  probe: "Globalping probe",
  simulated: "This server (simulated)",
};

/** A single address or host name against the configured GeoIP provider (ip-api.com, then GeoLite2), with the same map marker the target pages use. */
export function GeoLookup() {
  const theme = useDocumentTheme();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GeoLookupT | null>(null);
  const [provider, setProvider] = useState<GeoProvider>("auto");

  const lookup = async (q: string, via: GeoProvider = provider) => {
    const value = q.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.geoipLookup(value, via));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const place = useMemo<MapPlace | null>(() => {
    if (!result?.geo) return null;
    const label = result.kind === "address" || result.kind === "host" ? result.ip ?? result.query : "Monitor";
    const net = networkText(result.asn, result.as_name);
    return { id: "lookup", kind: result.kind === "address" || result.kind === "host" ? "destination" : "source", lat: result.geo.lat, lon: result.geo.lon, label, title: label, place: placeOf(result.geo), lines: [], hopNos: [], reached: true, accuracyKm: result.geo.accuracy_km, networks: net ? [net] : [] };
  }, [result]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">GeoIP lookup</h1>
        <p className="text-sm text-muted">Where the GeoIP provider (ip-api.com or the MaxMind GeoLite2 database) places an address. Use it to check a hop, a target, or the address this server is seen from, and to judge how far to trust the map.</p>
      </div>
      <form className="card flex flex-wrap items-end gap-3 p-4" onSubmit={(e) => { e.preventDefault(); void lookup(query); }}>
        <div className="min-w-[16rem] flex-1">
          <label className="label" htmlFor="geoip-query">IP address or host name</label>
          <input id="geoip-query" className="input font-mono" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="203.0.113.7 or www.example.com" autoFocus spellCheck={false} autoComplete="off" />
        </div>
        <button className="btn btn-primary" type="submit" disabled={busy || !query.trim()}><Search size={15} /> {busy ? "Looking up…" : "Look up"}</button>
        <button className="btn" type="button" disabled={busy} onClick={() => { setQuery("self"); void lookup("self"); }} title="Locate this server the way the path map does: by its own address, or by the public address it is seen from"><Server size={15} /> This server</button>
        <div className="basis-full">
          <span className="label" id="geoip-provider-label">Provider</span>
          <div className="seg" role="radiogroup" aria-labelledby="geoip-provider-label">
            {(Object.keys(GEO_PROVIDER_LABEL) as GeoProvider[]).map((k) => (
              <button key={k} type="button" data-active={provider === k} role="radio" aria-checked={provider === k} disabled={busy} onClick={() => { setProvider(k); if (result) void lookup(result.query, k); }}>
                {GEO_PROVIDER_LABEL[k]}
              </button>
            ))}
          </div>
          <div className="help">Automatic asks exactly as the map does: ip-api.com first when it is switched on, then the GeoLite2 databases. The other two ask one backend alone, so you can compare their answers; ip-api.com can be asked here even while its switch is off (the same request budget applies).</div>
        </div>
      </form>
      {error && <ErrorBanner message={error} />}
      {result && (
        <div className="card p-4">
          {!result.configured && !result.simulated && (
            <p className="mb-3 rounded-lg px-3 py-2 text-xs" style={{ background: "var(--degraded-soft)", color: "var(--degraded)" }}>
              No GeoIP provider is set up: no MaxMind licence key is saved and ip-api.com is switched off. Enable one under <Link to="/settings" className="underline">Settings</Link>.
            </p>
          )}
          {result.configured && !result.available && !result.simulated && (
            <p className="mb-3 rounded-lg px-3 py-2 text-xs" style={{ background: "var(--degraded-soft)", color: "var(--degraded)" }}>
              {result.provider === "ip-api" || (result.ip_api_enabled && !result.ip_api_ready && result.provider === "auto") ? "ip-api.com is paused after a failed request" + (result.provider === "ip-api" ? " and was asked alone. Lookups resume by themselves; see " : " and no GeoLite2 database is on disk to fall back to. Lookups resume by themselves; see ") : "The GeoLite2 databases have not been downloaded yet. Use Download now under "}<Link to="/settings" className="underline">Settings</Link>.
            </p>
          )}
          {result.provider === "auto" && result.ip_api_enabled && !result.ip_api_ready && result.available && !result.simulated && (
            <p className="mb-3 rounded-lg px-3 py-2 text-xs" style={{ background: "var(--degraded-soft)", color: "var(--degraded)" }}>
              ip-api.com is paused after a failed request; the GeoLite2 databases answered instead. See <Link to="/settings" className="underline">Settings</Link>.
            </p>
          )}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
            <dl className="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm" aria-label="Lookup result">
              <dt className="text-muted">Looked up</dt><dd className="font-mono break-all">{result.query}</dd>
              <dt className="text-muted">Provider</dt><dd>{GEO_PROVIDER_LABEL[result.provider]}{result.provider === "auto" ? " (as the map)" : " alone"}</dd>
              <dt className="text-muted">Kind</dt><dd>{KIND_LABEL[result.kind]}</dd>
              {result.host && result.kind === "host" && <><dt className="text-muted">Resolves to</dt><dd className="font-mono">{result.ip ?? "–"}</dd></>}
              {result.kind !== "host" && result.ip && <><dt className="text-muted">Address</dt><dd className="font-mono">{result.ip}</dd></>}
              <dt className="text-muted">Location</dt>
              <dd>{result.geo ? placeOf(result.geo) || "unknown place" : <span className="text-down">{result.note ?? "not located"}</span>}</dd>
              {result.geo && (
                <>
                  <dt className="text-muted">Coordinates</dt><dd className="font-mono">{result.geo.lat.toFixed(4)}, {result.geo.lon.toFixed(4)}</dd>
                  <dt className="text-muted">Accuracy</dt><dd>{result.geo.accuracy_km !== null ? `${accuracyText(result.geo.accuracy_km)} radius` : "not stated by the database"}</dd>
                  {result.geo.country_code && <><dt className="text-muted">Country code</dt><dd className="font-mono">{result.geo.country_code}</dd></>}
                </>
              )}
              {result.ip && (
                <>
                  <dt className="text-muted">Network</dt>
                  <dd>{networkText(result.asn, result.as_name) || <span className="text-faint">{result.note === "private address" ? "none (private address)" : !result.asn_available && !result.simulated ? (result.ip_api_enabled ? "unknown (ip-api.com unavailable, no ASN database)" : "unknown (ASN database not downloaded)") : "not in database"}</span>}</dd>
                </>
              )}
              <dt className="text-muted">Source</dt>
              <dd>{result.simulated ? "simulated (no provider is asked in simulation mode)" : result.geo?.provider === "ip-api.com" ? `ip-api.com (batched lookup)${result.build_epoch ? `, GeoLite2 City built ${result.build_epoch.slice(0, 10)} as fallback` : ""}` : result.build_epoch ? `GeoLite2 City built ${result.build_epoch.slice(0, 10)}${result.asn_available ? " and GeoLite2 ASN" : ""}${result.ip_api_enabled ? " (ip-api.com did not place this address)" : ""}` : result.ip_api_enabled ? "ip-api.com, no GeoLite2 database as fallback" : "none"}</dd>
            </dl>
            <div>
              {place ? (
                <Suspense fallback={<div className="flex items-center justify-center text-sm text-faint" style={{ height: 280 }}>Loading map…</div>}>
                  <PathMap places={[place]} paths={[]} dark={theme !== "light"} height={280} />
                </Suspense>
              ) : (
                <div className="flex items-center justify-center rounded-lg border border-dashed border-border-strong text-sm text-faint" style={{ height: 280 }}>
                  <span className="inline-flex items-center gap-2"><MapPin size={15} /> Nothing to place on a map.</span>
                </div>
              )}
            </div>
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-faint">
            GeoIP data knows a city at best and often registers an address at its network operator's head office. The accuracy radius is GeoLite2's own estimate (ip-api.com states none); a large radius means the marker could be anywhere inside it.
          </p>
        </div>
      )}
    </div>
  );
}
