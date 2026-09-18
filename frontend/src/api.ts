// Typed client for the MTR Tracker HTTP API.

export type Status = "up" | "degraded" | "down" | "pending" | "paused";
export type Protocol = "icmp" | "udp" | "tcp";
export type IpVersion = "auto" | "4" | "6";
export type ProbeType = "mtr" | "ping" | "http" | "tcp" | "dns" | "globalping";
export type HttpMethod = "GET" | "HEAD" | "POST" | "PUT" | "PATCH" | "DELETE" | "OPTIONS";
export type DnsRecordType = "A" | "AAAA" | "CNAME" | "MX" | "NS" | "TXT" | "SOA" | "PTR" | "SRV";
export type GlobalpingMeasurement = "ping" | "traceroute" | "mtr" | "dns" | "http";
export type GlobalpingHttpMethod = "GET" | "HEAD" | "OPTIONS";
export type GlobalpingHttpProtocol = "HTTPS" | "HTTP" | "HTTP2";

export interface HttpOptions {
  method: HttpMethod;
  expected_status: string;
  keyword: string;
  keyword_absent: boolean;
  json_path: string;
  json_expected: string;
  headers: Record<string, string>;
  body: string;
  timeout_sec: number;
  verify_tls: boolean;
  follow_redirects: boolean;
  tls_warn_days: number;
  /** Record the certificate (subject, issuer, validity, names, protocol) with every run. */
  tls_info: boolean;
}
export interface PingOptions { timeout_sec: number }
export interface TcpOptions { timeout_sec: number }
export interface DnsOptions {
  record_type: DnsRecordType;
  resolver: string;
  expected: string;
  timeout_sec: number;
  /** Query a random label under the name each run, so no cache can answer (measures the uncached lookup). */
  random_prefix: boolean;
}
export interface GlobalpingOptions {
  measurement: GlobalpingMeasurement;
  /** Globalping "magic" location: country, city, continent, region, ASN, network or cloud region; "world" = any probe. */
  location: string;
  /** Probes to use at that location (ping, dns, http); traceroute and mtr always use one. */
  probes: number;
  /** dns: record type, resolver the probe should ask (empty = its own) and an expected answer substring. */
  record_type: DnsRecordType;
  resolver: string;
  expected: string;
  /** http: request path, method, protocol, acceptable status codes and a body keyword. */
  path: string;
  http_method: GlobalpingHttpMethod;
  http_protocol: GlobalpingHttpProtocol;
  expected_status: string;
  keyword: string;
}
export type ProbeOptions = Partial<HttpOptions & PingOptions & TcpOptions & DnsOptions & GlobalpingOptions>;

export const PROBE_TYPE_LABEL: Record<ProbeType, string> = { mtr: "MTR", ping: "Ping", http: "HTTP(S)", tcp: "TCP port", dns: "DNS", globalping: "Globalping" };

export const DEFAULT_OPTIONS: Record<ProbeType, ProbeOptions> = {
  mtr: {},
  ping: { timeout_sec: 2 },
  http: { method: "GET", expected_status: "200-299", keyword: "", keyword_absent: false, json_path: "", json_expected: "", headers: {}, body: "", timeout_sec: 10, verify_tls: true, follow_redirects: true, tls_warn_days: 14, tls_info: true },
  tcp: { timeout_sec: 5 },
  dns: { record_type: "A", resolver: "", expected: "", timeout_sec: 5, random_prefix: false },
  globalping: { measurement: "ping", location: "world", probes: 1, record_type: "A", resolver: "", expected: "", path: "/", http_method: "GET", http_protocol: "HTTPS", expected_status: "200-299", keyword: "" },
};

/** Per-type default latency alert threshold (ms); mirrors LATENCY_ALERT_DEFAULT in backend/app/models.py. */
export const LATENCY_ALERT_DEFAULT: Record<ProbeType, number> = { mtr: 200, ping: 200, http: 1500, tcp: 500, dns: 500, globalping: 200 };

/** Certificate details recorded by an HTTP(S) check (details.tls). */
export interface TlsInfo {
  subject: string | null;
  subject_org: string | null;
  issuer: string | null;
  issuer_cn: string | null;
  not_before: string | null;
  not_after: string | null;
  days_left: number | null;
  san: string[];
  serial: string | null;
  protocol: string | null;
  cipher: string | null;
}

/** Where a Globalping probe sits; shared by every measurement's details.probes[] entries. */
export interface GlobalpingProbeBase {
  label: string;
  continent: string | null;
  country: string | null;
  city: string | null;
  asn: number | null;
  network: string | null;
  status: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

/** One remote probe of a Globalping ping run. */
export interface GlobalpingProbe extends GlobalpingProbeBase {
  resolved: string | null;
  sent: number | null;
  received: number | null;
  loss: number | null;
  min: number | null;
  avg: number | null;
  max: number | null;
  rtts: number[];
}

/** One remote probe of a Globalping dns run. */
export interface GlobalpingDnsProbe extends GlobalpingProbeBase {
  rcode: string | null;
  resolver: string | null;
  total_ms: number | null;
  answers: { name: string | null; type: string | null; ttl: number | null; value: string | null }[];
  passed: boolean;
  reason: string | null;
}

/** One remote probe of a Globalping http run. */
export interface GlobalpingHttpProbe extends GlobalpingProbeBase {
  status_code: number | null;
  status_name: string | null;
  resolved: string | null;
  total_ms: number | null;
  timings: { dns: number | null; tcp: number | null; tls: number | null; firstByte: number | null; download: number | null };
  server: string | null;
  content_type: string | null;
  truncated: boolean;
  tls: TlsInfo | null;
  passed: boolean;
  reason: string | null;
}

export interface Run {
  id: number;
  target_id: number;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  status: "ok" | "error";
  error: string | null;
  src: string | null;
  dst_ip: string | null;
  reached: boolean;
  hop_count: number;
  sent: number | null;
  loss_pct: number | null;
  last_ms: number | null;
  avg_ms: number | null;
  best_ms: number | null;
  worst_ms: number | null;
  stdev_ms: number | null;
  jitter_avg_ms: number | null;
  jitter_max_ms: number | null;
  route_hash: string | null;
  route_changed: boolean;
  command: string | null;
  details: Record<string, unknown> | null;
}

export interface Hop {
  id?: number;
  run_id?: number;
  hop_no: number;
  ip: string | null;
  hostname: string | null;
  asn: string | null;
  loss_pct: number;
  sent: number;
  received: number;
  last_ms: number | null;
  avg_ms: number | null;
  best_ms: number | null;
  worst_ms: number | null;
  stdev_ms: number | null;
  gmean_ms: number | null;
  jitter_ms: number | null;
  jitter_avg_ms: number | null;
  jitter_max_ms: number | null;
  jitter_int_ms: number | null;
}

export interface RunDetail extends Run {
  target_name: string;
  target_host: string;
  target_type: ProbeType;
  hops: Hop[];
  prev_run_id: number | null;
  next_run_id: number | null;
}

export interface SparkPoint {
  t: string;
  avg: number | null;
  loss: number | null;
  reached: boolean;
}

export interface TimelineBucket {
  s: "up" | "degraded" | "down";
  n: number;
  avg: number | null;
}

export interface OverviewSeries {
  range_sec: number;
  bucket_sec: number;
  targets: { id: number; name: string; host: string; enabled: boolean; points: { t: string; avg: number | null; loss: number | null; ok: boolean; n: number }[] }[];
}

export interface HourlyBucket {
  t: string;
  n: number;
  ok_n: number;
  avg: number | null;
  worst: number | null;
  loss: number | null;
  max_loss: number | null;
  jitter: number | null;
}

export interface RouteSegment {
  hash: string;
  start: string;
  end: string;
  runs: number;
  first_run_id: number;
  hops: number;
  index: number;
}

export interface RouteInfo {
  hash: string;
  index: number;
  runs: number;
  hops: number;
  share_pct: number;
  first_seen: string;
  last_seen: string;
  reached: number;
  example_run_id: number;
  /** Stored route hashes folded into this route: 1 when every run answered at every hop, more when silent hops were merged. */
  variants: number;
}

export interface Routes {
  range_sec: number;
  since: string;
  total_runs: number;
  segments: RouteSegment[];
  routes: RouteInfo[];
}

export interface RangeStats {
  range_sec: number;
  runs: number;
  ok_runs: number;
  failed_runs: number;
  availability_pct: number | null;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  best_ms: number | null;
  worst_ms: number | null;
  loss_pct: number | null;
  max_loss_pct: number | null;
  jitter_ms: number | null;
  route_changes: number;
  hop_count_min: number | null;
  hop_count_max: number | null;
  events: number;
}

export interface Target {
  id: number;
  name: string;
  host: string;
  type: ProbeType;
  options: ProbeOptions;
  description: string;
  tags: string[];
  interval_sec: number;
  count: number;
  probe_interval: number;
  protocol: Protocol;
  port: number | null;
  packet_size: number;
  ip_version: IpVersion;
  max_hops: number;
  enabled: boolean;
  alert_loss_pct: number;
  alert_latency_ms: number;
  created_at: string;
  updated_at: string;
  next_run_at: string | null;
  last_status: Status;
  latest_run: Run | null;
  stats_24h: {
    runs: number;
    availability_pct: number | null;
    avg_ms: number | null;
    loss_pct: number | null;
    route_changes: number;
  };
  sparkline: SparkPoint[];
  timeline: { bucket_sec: number; since: string; buckets: (TimelineBucket | null)[] };
  stats?: RangeStats;
  running?: boolean;
}

export type TargetInput = Omit<
  Target,
  "id" | "created_at" | "updated_at" | "next_run_at" | "last_status" | "latest_run" | "stats_24h" | "sparkline" | "timeline" | "stats" | "running"
>;

/** The editable fields of a target, as the API accepts them on create and update. */
export function targetInput(t: Target): TargetInput {
  return {
    name: t.name,
    host: t.host,
    type: t.type || "mtr",
    options: { ...(t.options || {}) },
    description: t.description,
    tags: [...t.tags],
    interval_sec: t.interval_sec,
    count: t.count,
    probe_interval: t.probe_interval,
    protocol: t.protocol,
    port: t.port,
    packet_size: t.packet_size,
    ip_version: t.ip_version,
    max_hops: t.max_hops,
    enabled: t.enabled,
    alert_loss_pct: t.alert_loss_pct,
    alert_latency_ms: t.alert_latency_ms,
  };
}

/** A copy of a target ready for the "add" form: every setting of the original and a "(copy)" name. */
export function cloneInput(t: Target): TargetInput {
  return { ...targetInput(t), name: `${t.name} (copy)` };
}

export interface SeriesPoint {
  t: string;
  run_id: number | null;
  avg: number | null;
  best: number | null;
  worst: number | null;
  loss: number | null;
  max_loss?: number | null;
  jitter: number | null;
  hops: number | null;
  ok: boolean;
  route_changed: boolean;
  n: number;
}

export interface Series {
  range_sec: number;
  bucket_sec: number | null;
  points: SeriesPoint[];
}

export interface HopHistoryCell {
  hop: number;
  ip: string | null;
  hostname: string | null;
  loss: number | null;
  avg: number | null;
  best: number | null;
  worst: number | null;
  jitter: number | null;
}

export interface HopHistory {
  runs: { run_id: number; t: string; reached: boolean; hops: HopHistoryCell[] }[];
  max_hops: number;
}

export interface HopSummaryEntry {
  ip: string | null;
  hostname: string | null;
  asn: string | null;
  runs: number;
  share_pct: number | null;
  sent?: number;
  received?: number;
  loss_pct: number;
  max_loss_pct: number | null;
  avg_ms: number | null;
  best_ms: number | null;
  worst_ms: number | null;
  stdev_ms: number | null;
  jitter_ms: number | null;
  jitter_max_ms: number | null;
}

export interface HopSummary {
  total_runs: number;
  /** silent_runs: runs in which the hop answered nothing; counted as loss on the primary address, not as an alternate. */
  hops: { hop: number; primary: HopSummaryEntry; alternates: HopSummaryEntry[]; silent_runs?: number }[];
}

export interface Event {
  id: number;
  target_id: number | null;
  run_id: number | null;
  kind: "down" | "recovered" | "degraded" | "route_change" | string;
  severity: "info" | "warning" | "critical";
  message: string;
  details: Record<string, unknown> | null;
  created_at: string;
  target_name?: string;
  target_host?: string;
}

export interface Settings {
  retention_days: number;
  asn_lookup: boolean;
  reverse_dns: boolean;
  webhook_url: string;
  webhook_events: string[];
  pushover_enabled: boolean;
  pushover_user_key: string;
  pushover_api_token: string;
  pushover_device: string;
  pushover_sound: string;
  pushover_priority: "auto" | "-2" | "-1" | "0" | "1" | "2";
  pushover_events: string[];
  base_url: string;
  site_name: string;
  /** tag -> #rrggbb; tags without an entry get an automatic colour (see autoTagColor in utils.ts). */
  tag_colors: Record<string, string>;
  /** Optional Globalping API token (raises the rate limits for the globalping probe type). */
  globalping_token: string;
  /** MaxMind account ID (optional, digits) and licence key; the key enables the GeoLite2 download and the map on target pages. */
  maxmind_account_id: string;
  maxmind_license_key: string;
}

/** State of the MaxMind GeoLite2 database on the server (GET /api/geoip/status). */
export interface GeoIpStatus {
  configured: boolean;
  available: boolean;
  simulated: boolean;
  edition: string;
  path: string;
  build_epoch: string | null;
  downloaded_at: string | null;
  last_attempt: string | null;
  last_success: string | null;
  last_error: string | null;
  updating: boolean;
  refresh_after_sec: number;
}

/** A located address as returned by the geo endpoint. */
export interface GeoPoint {
  lat: number;
  lon: number;
  city: string | null;
  region: string | null;
  country: string | null;
  country_code: string | null;
  accuracy_km: number | null;
}

/** Where a run was launched from: this server (its own or its public address), a Globalping probe, or the simulator. */
export interface GeoSource {
  kind: "monitor" | "public_ip" | "probe" | "simulated";
  label: string;
  ip: string | null;
  geo: GeoPoint | null;
  note: string | null;
}

export interface GeoHop {
  hop_no: number;
  ip: string | null;
  hostname: string | null;
  asn: string | null;
  avg_ms: number | null;
  loss_pct: number | null;
  geo: GeoPoint | null;
  /** Why there is no location: "private address", "not in database", "no database", "no response". */
  note: string | null;
}

export interface GeoDestination {
  ip: string;
  host: string;
  reached: boolean;
  geo: GeoPoint | null;
  note: string | null;
}

/** Geolocation of the latest completed run (GET /api/targets/{id}/geo); `enabled` is false without a MaxMind key. */
export interface PathGeo {
  enabled: boolean;
  available: boolean;
  run_id: number | null;
  sources: GeoSource[];
  hops: GeoHop[];
  destination: GeoDestination | null;
}

export interface TagInfo {
  name: string;
  count: number;
  color: string | null;
}

export interface SystemStatus {
  app: string;
  version: string;
  time: string;
  uptime_sec: number;
  simulate: boolean;
  mtr_version: string | null;
  mtr_binary: string;
  /** Smallest probe interval mtr accepts for this server (1 s unless it runs as root). */
  min_probe_interval: number;
  max_concurrent_runs: number;
  active_runs: number[];
  runs_completed_since_start: number;
  db_size_bytes: number;
  /** Only present when the request carried the API token (or no token is configured). */
  db_path: string | null;
  targets: { total: number; enabled: number; up: number; degraded: number; down: number; pending: number };
  runs_24h: { total: number; ok: number };
  runs_total: number;
  events_24h: number;
}

export interface ProbeResult {
  host: string;
  dst_ip: string;
  src: string | null;
  reached: boolean;
  duration_ms: number;
  command: string;
  hops: Hop[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const TOKEN_KEY = "mtr-tracker.token";

export function getApiToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setApiToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/** Fired when the server rejects a write for lack of a valid API token; the layout opens a prompt. */
export const AUTH_REQUIRED_EVENT = "mtr-tracker:auth-required";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getApiToken();
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init?.headers || {}) },
  });
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
      else if (Array.isArray(body?.detail)) message = body.detail.map((d: { msg: string; loc?: string[] }) => `${(d.loc || []).slice(-1)[0] ?? ""}: ${d.msg}`).join("; ");
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const api = {
  status: () => request<SystemStatus>("/api/status"),
  settings: () => request<Settings>("/api/settings"),
  updateSettings: (patch: Partial<Settings>) => request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),
  testNotification: (channel: "webhook" | "pushover", settings: Partial<Settings>) =>
    request<{ ok: boolean; channel: string }>("/api/notifications/test", { method: "POST", body: JSON.stringify({ channel, settings }) }),
  geoipStatus: () => request<GeoIpStatus>("/api/geoip/status"),
  geoipUpdate: () => request<GeoIpStatus>("/api/geoip/update", { method: "POST" }),

  targets: () => request<Target[]>("/api/targets"),
  tags: () => request<TagInfo[]>("/api/tags"),
  target: (id: number, range: string) => request<Target>(`/api/targets/${id}?range=${encodeURIComponent(range)}`),
  createTarget: (body: TargetInput) => request<Target>("/api/targets", { method: "POST", body: JSON.stringify(body) }),
  updateTarget: (id: number, patch: Partial<TargetInput>) =>
    request<Target>(`/api/targets/${id}`, { method: "PUT", body: JSON.stringify(patch) }),
  deleteTarget: (id: number) => request<void>(`/api/targets/${id}`, { method: "DELETE" }),
  exportTargets: () => request<TargetInput[]>("/api/targets/export"),
  importTargets: (targets: TargetInput[], mode: "upsert" | "create" | "replace") =>
    request<{ created: number; updated: number; total: number }>("/api/targets/import", { method: "POST", body: JSON.stringify({ targets, mode }) }),
  bulk: (action: "pause" | "resume" | "run" | "delete", ids: number[]) =>
    request<{ action: string; affected: number[] }>("/api/targets/bulk", { method: "POST", body: JSON.stringify({ action, ids }) }),
  runNow: (id: number) => request<{ queued: boolean; already_running: boolean }>(`/api/targets/${id}/run`, { method: "POST" }),

  runs: (id: number, params: { limit?: number; offset?: number; range?: string; status?: string }) => {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    if (params.range) q.set("range", params.range);
    if (params.status) q.set("status", params.status);
    return request<{ total: number; items: Run[] }>(`/api/targets/${id}/runs?${q}`);
  },
  series: (id: number, range: string) => request<Series>(`/api/targets/${id}/series?range=${encodeURIComponent(range)}`),
  hopHistory: (id: number, range: string, maxRuns = 120) =>
    request<HopHistory>(`/api/targets/${id}/hops/history?range=${encodeURIComponent(range)}&max_runs=${maxRuns}`),
  hopSummary: (id: number, range: string) => request<HopSummary>(`/api/targets/${id}/hops/summary?range=${encodeURIComponent(range)}`),
  overview: (range: string) => request<OverviewSeries>(`/api/overview/series?range=${encodeURIComponent(range)}`),
  hourly: (id: number, range: string) => request<{ range_sec: number; hours: HourlyBucket[] }>(`/api/targets/${id}/hourly?range=${encodeURIComponent(range)}`),
  routes: (id: number, range: string) => request<Routes>(`/api/targets/${id}/routes?range=${encodeURIComponent(range)}`),
  geo: (id: number) => request<PathGeo>(`/api/targets/${id}/geo`),
  targetEvents: (id: number, range?: string) =>
    request<Event[]>(`/api/targets/${id}/events?limit=200${range ? `&range=${encodeURIComponent(range)}` : ""}`),

  run: (id: number) => request<RunDetail>(`/api/runs/${id}`),
  runReportUrl: (id: number) => `/api/runs/${id}/report`,

  events: (params: { limit?: number; offset?: number; kind?: string; severity?: string; target_id?: number; range?: string }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    return request<{ total: number; items: Event[] }>(`/api/events?${q}`);
  },
  clearEvents: (target_id?: number) => request<void>(`/api/events${target_id ? `?target_id=${target_id}` : ""}`, { method: "DELETE" }),

  probe: (body: { host: string; count: number; protocol: Protocol; port: number | null; ip_version: IpVersion; max_hops: number }) =>
    request<ProbeResult>("/api/probe", { method: "POST", body: JSON.stringify(body) }),
};
