import { Plus, X } from "lucide-react";
import { HelpTip } from "./Popover";

/** The query parameter the backend's cache buster appends (`models.CACHE_BUSTER_PARAM`). */
export const CACHE_BUSTER_PARAM = "mtr_tracker_cachebuster";
/** What the HTTP check sends as User-Agent unless a header replaces it (`probes.DEFAULT_USER_AGENT`). */
export const DEFAULT_USER_AGENT = "MTR-Tracker/1.0";
/** Limits shared with `models.clean_headers`. */
export const MAX_HTTP_HEADERS = 50;
const HEADER_NAME_RE = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const FORBIDDEN_HEADERS = ["content-length", "transfer-encoding"];

export interface HeaderRow {
  id: number;
  name: string;
  value: string;
}

let nextRowId = 1;
const row = (name = "", value = ""): HeaderRow => ({ id: nextRowId++, name, value });

/** Groups of the User-Agent preset dropdown, in display order. */
export const USER_AGENT_GROUPS = ["Desktop browsers", "Mobile browsers", "Command-line tools and libraries", "Monitoring services", "Search engine crawlers"] as const;
export type UserAgentGroup = (typeof USER_AGENT_GROUPS)[number];

/**
 * Identities offered for the User-Agent header. They are ordinary current strings, not tied to this machine; each value
 * must be unique because the dropdown is keyed on it, and a stored value that matches one shows that preset as chosen.
 */
export const USER_AGENT_PRESETS: { label: string; value: string; group: UserAgentGroup }[] = [
  { group: "Desktop browsers", label: "Chrome on Windows", value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36" },
  { group: "Desktop browsers", label: "Chrome on macOS", value: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36" },
  { group: "Desktop browsers", label: "Chrome on Linux", value: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36" },
  { group: "Desktop browsers", label: "Edge on Windows", value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0" },
  { group: "Desktop browsers", label: "Firefox on Windows", value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0" },
  { group: "Desktop browsers", label: "Firefox on macOS", value: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:143.0) Gecko/20100101 Firefox/143.0" },
  { group: "Desktop browsers", label: "Firefox on Linux", value: "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0" },
  { group: "Desktop browsers", label: "Safari on macOS", value: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15" },
  { group: "Desktop browsers", label: "Opera on Windows", value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 OPR/124.0.0.0" },
  { group: "Mobile browsers", label: "Safari on iPhone", value: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1" },
  { group: "Mobile browsers", label: "Safari on iPad", value: "Mozilla/5.0 (iPad; CPU OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1" },
  { group: "Mobile browsers", label: "Chrome on Android", value: "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36" },
  { group: "Mobile browsers", label: "Samsung Internet on Android", value: "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36" },
  { group: "Mobile browsers", label: "Firefox on Android", value: "Mozilla/5.0 (Android 15; Mobile; rv:143.0) Gecko/143.0 Firefox/143.0" },
  { group: "Command-line tools and libraries", label: "curl", value: "curl/8.15.0" },
  { group: "Command-line tools and libraries", label: "Wget", value: "Wget/1.25.0" },
  { group: "Command-line tools and libraries", label: "PowerShell", value: "Mozilla/5.0 (Windows NT 10.0; Microsoft Windows 10.0.26100; en-US) PowerShell/7.5.3" },
  { group: "Command-line tools and libraries", label: "Python requests", value: "python-requests/2.32.5" },
  { group: "Command-line tools and libraries", label: "Go HTTP client", value: "Go-http-client/2.0" },
  { group: "Command-line tools and libraries", label: "Postman", value: "PostmanRuntime/7.48.0" },
  { group: "Monitoring services", label: "Uptime Kuma", value: "Uptime-Kuma/2.0.0" },
  { group: "Monitoring services", label: "UptimeRobot", value: "Mozilla/5.0+(compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)" },
  { group: "Monitoring services", label: "Pingdom", value: "Pingdom.com_bot_version_1.4_(http://www.pingdom.com/)" },
  { group: "Search engine crawlers", label: "Googlebot", value: "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" },
  { group: "Search engine crawlers", label: "Googlebot (smartphone)", value: "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" },
  { group: "Search engine crawlers", label: "Bingbot", value: "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)" },
];

/** One-click additions; a header that is already listed gets the value instead of a second row. */
const HEADER_PRESETS: { label: string; name: string; value: string }[] = [
  { label: "Accept JSON", name: "Accept", value: "application/json" },
  { label: "Accept-Language", name: "Accept-Language", value: "en-US,en;q=0.9" },
  { label: "No cache", name: "Cache-Control", value: "no-cache" },
  { label: "Bearer token", name: "Authorization", value: "Bearer " },
];

const COMMON_HEADER_NAMES = ["User-Agent", "Accept", "Accept-Language", "Accept-Encoding", "Authorization", "Cache-Control", "Pragma", "Cookie", "Content-Type", "Host", "Origin", "Referer", "X-Api-Key", "X-Forwarded-For", "X-Requested-With"];

/** Editable rows for a stored header object (one empty row when there is none, so the fields are visible). */
export function headerRows(headers: Record<string, string> | undefined): HeaderRow[] {
  const rows = Object.entries(headers ?? {}).map(([k, v]) => row(k, v));
  return rows.length ? rows : [row()];
}

/** The header object to save, or the first problem as a sentence. Blank rows are ignored; checks mirror the backend's. */
export function headersFromRows(rows: HeaderRow[]): { headers: Record<string, string> } | { error: string } {
  const headers: Record<string, string> = {};
  const seen = new Set<string>();
  for (const r of rows) {
    const name = r.name.trim();
    const value = r.value.trim();
    if (!name && !value) continue;
    if (!name) return { error: `The header with the value "${value}" needs a name.` };
    if (!HEADER_NAME_RE.test(name)) return { error: `"${name}" is not a valid header name: use letters, digits and - (no spaces or colons).` };
    if (FORBIDDEN_HEADERS.includes(name.toLowerCase())) return { error: `The ${name} header is set automatically and cannot be overridden.` };
    if (seen.has(name.toLowerCase())) return { error: `The header "${name}" is listed twice; header names ignore case.` };
    // eslint-disable-next-line no-control-regex
    if (!/^[\x20-\x7e\t]*$/.test(value)) return { error: `The value of the header "${name}" must be plain ASCII text on one line.` };
    seen.add(name.toLowerCase());
    headers[name] = value;
  }
  if (Object.keys(headers).length > MAX_HTTP_HEADERS) return { error: `At most ${MAX_HTTP_HEADERS} headers.` };
  return { headers };
}

/** The URL as one run would request it with the cache buster on (the token is an example; every run draws a new one). */
export function cacheBustExample(host: string, token = "3f9c2a71b0d4e8a6"): string {
  let raw = host.trim() || "example.com/status";
  if (!/^https?:\/\//i.test(raw)) raw = `https://${raw}`;
  const [base, fragment] = raw.split(/#(.*)/s, 2);
  const joiner = base.includes("?") ? (base.endsWith("?") || base.endsWith("&") ? "" : "&") : "?";
  return `${base}${joiner}${CACHE_BUSTER_PARAM}=${token}${fragment !== undefined ? `#${fragment}` : ""}`;
}

/** The HTTP check's request headers: name/value rows, quick additions and a User-Agent preset picker. */
export function HeaderEditor({ rows, onChange }: { rows: HeaderRow[]; onChange: (rows: HeaderRow[]) => void }) {
  const uaRow = rows.find((r) => r.name.trim().toLowerCase() === "user-agent");
  const uaPreset = !uaRow ? "" : (USER_AGENT_PRESETS.find((p) => p.value === uaRow.value.trim())?.value ?? "custom");
  const update = (id: number, patch: Partial<HeaderRow>) => onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const remove = (id: number) => {
    const rest = rows.filter((r) => r.id !== id);
    onChange(rest.length ? rest : [row()]);
  };
  /** Set a header's value, replacing an existing row of that name (any case) or filling the first blank row. */
  const upsert = (name: string, value: string) => {
    const existing = rows.find((r) => r.name.trim().toLowerCase() === name.toLowerCase());
    if (existing) return update(existing.id, { value });
    const blank = rows.find((r) => !r.name.trim() && !r.value.trim());
    if (blank) return update(blank.id, { name, value });
    onChange([...rows, row(name, value)]);
  };
  const pickUserAgent = (value: string) => {
    if (value === "custom") return;
    if (value) return upsert("User-Agent", value);
    if (uaRow) remove(uaRow.id);
  };

  return (
    <div className="sm:col-span-2" role="group" aria-labelledby="http-headers-label">
      <div className="mb-1 flex items-center gap-1">
        <span id="http-headers-label" className="label mb-0">Request headers (optional)</span>
        <HelpTip label="request headers">
          <p>Headers are name/value pairs sent with the request, before any body. Servers use them to decide what to answer: <code>User-Agent</code> names the client, <code>Accept</code> asks for a format such as JSON, <code>Authorization</code> carries a token, <code>Host</code> picks a virtual host on a shared address.</p>
          <p className="mt-2">The <b>User-Agent preset</b> dropdown offers common identities in groups. <b>Desktop</b> and <b>mobile browsers</b> get past sites that block or redirect unknown clients and show the page a visitor on that device gets. <b>Command-line tools</b> reproduce what a script or API client is served. <b>Monitoring services</b> match firewall or WAF rules that already allow those checkers. <b>Search engine crawlers</b> show what a site serves to bots, but many sites and CDNs verify real crawlers by their address, so a check posing as Googlebot or Bingbot may be blocked or challenged. The strings are recent versions written into the app; edit the value for an exact one.</p>
          <p className="mt-2">Every header listed here is sent on every run of this check, and on each redirect it follows (httpx drops <code>Authorization</code> when a redirect leads to another host). Names ignore case. A header you add replaces the built-in one of the same name, so a <code>User-Agent</code> row replaces <code>{DEFAULT_USER_AGENT}</code>; <code>Content-Length</code> and <code>Transfer-Encoding</code> are always computed from the request and cannot be set.</p>
          <p className="mt-2">Each run records the headers it sent under <b>Request headers</b> in its details, with the values of <code>Authorization</code>, <code>Cookie</code> and API-key headers masked. The values are stored with the target in plain text, so anyone who can read this target's settings can read them.</p>
        </HelpTip>
      </div>
      <div className="help mt-0 mb-2" data-testid="headers-help">
        Sent with every request, for example a browser <code>User-Agent</code> for a site that blocks unknown clients, <code>Accept: application/json</code> for an API, or an <code>Authorization</code> token. Without a <code>User-Agent</code> header the check identifies itself as <code>{DEFAULT_USER_AGENT}</code>.
      </div>
      <datalist id="http-header-names">
        {COMMON_HEADER_NAMES.map((n) => <option key={n} value={n} />)}
      </datalist>
      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={r.id} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] gap-2">
            <input className="input font-mono" aria-label={`Header ${i + 1} name`} list="http-header-names" value={r.name} onChange={(e) => update(r.id, { name: e.target.value })} placeholder="Name, e.g. User-Agent" spellCheck={false} autoComplete="off" />
            <input className="input font-mono" aria-label={`Header ${i + 1} value`} value={r.value} onChange={(e) => update(r.id, { value: e.target.value })} placeholder="Value" spellCheck={false} autoComplete="off" />
            <button type="button" className="btn btn-ghost btn-sm" aria-label={`Remove header ${i + 1}`} title="Remove header" onClick={() => remove(r.id)}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <button type="button" className="btn btn-sm" onClick={() => onChange([...rows, row()])} disabled={rows.length >= MAX_HTTP_HEADERS}>
          <Plus size={14} /> Add header
        </button>
        <select className="input w-auto py-1 text-xs" aria-label="User-Agent preset" value={uaPreset} onChange={(e) => pickUserAgent(e.target.value)}>
          <option value="">User-Agent: default ({DEFAULT_USER_AGENT})</option>
          {USER_AGENT_GROUPS.map((group) => (
            <optgroup key={group} label={group}>
              {USER_AGENT_PRESETS.filter((p) => p.group === group).map((p) => <option key={p.label} value={p.value}>User-Agent: {p.label}</option>)}
            </optgroup>
          ))}
          {uaPreset === "custom" && <option value="custom">User-Agent: custom</option>}
        </select>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Common headers">
          <span className="text-muted">Add:</span>
          {HEADER_PRESETS.map((p) => (
            <button key={p.label} type="button" className="btn btn-sm" title={`${p.name}: ${p.value}`} onClick={() => upsert(p.name, p.value)}>
              {p.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** The cache buster switch with its explanation and the URL a run would actually request. */
export function CacheBusterField({ checked, host, onChange }: { checked: boolean; host: string; onChange: (on: boolean) => void }) {
  return (
    <div className="sm:col-span-2">
      <div className="flex items-center gap-1">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> Cache buster
        </label>
        <HelpTip label="the cache buster">
          <p>Caches (a CDN such as Cloudflare or Akamai, a reverse proxy such as Varnish or nginx, a corporate proxy, the application's own page cache) store a response under its full URL and hand the stored copy to the next request for the same URL. A check that is answered from a cache measures the cache: it can stay green while the server behind it is down, and its response time is the edge's, not the origin's.</p>
          <p className="mt-2">With the cache buster on, every run appends <code>{CACHE_BUSTER_PARAM}=</code> and 16 new random hexadecimal characters to the URL's query string (after any query the URL already has). No cache has seen that URL before, so it must forward the request to the origin server, and the status, keyword, JSON query and response time all describe the origin. The idea and the behaviour are the same as Uptime Kuma's cache buster, which appends <code>uptime_kuma_cachebuster</code>.</p>
          <p className="mt-2">Things to know: some applications refuse unknown query parameters, and signed URLs (S3 or CDN tokens) stop matching their signature, so the check may then fail. A CDN set to ignore the query string still answers from its cache; add a <code>Cache-Control: no-cache</code> header for those as well. Every run can leave one unused entry in a cache, and origin responses are usually slower than cached ones, so the latency chart will rise when you switch this on. Each run's details show the URL it requested.</p>
        </HelpTip>
      </div>
      <div className="help ml-6" data-testid="cache-buster-help">
        Adds a random <code>{CACHE_BUSTER_PARAM}</code> parameter to the URL on every run, so CDNs, proxies and server caches cannot answer from a stored copy and the check measures the origin server. {checked ? "Each run requests, for example:" : "When on, a run requests, for example:"}
        <code className="mt-1 block break-all" data-testid="cache-buster-example">{cacheBustExample(host)}</code>
      </div>
    </div>
  );
}
