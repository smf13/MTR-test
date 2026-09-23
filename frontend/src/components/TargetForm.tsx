import { useEffect, useMemo, useState } from "react";
import { api, DEFAULT_OPTIONS, DNS_TRANSPORT_LABEL, LATENCY_ALERT_DEFAULT, PROBE_TYPE_LABEL, targetInput, type DnsRecordType, type DnsTransport, type GlobalpingHttpMethod, type GlobalpingHttpProtocol, type GlobalpingMeasurement, type HttpMethod, type IpVersion, type ProbeOptions, type ProbeType, type Protocol, type Target, type TargetInput } from "../api";
import { Modal } from "./Modal";
import { NumberInput } from "./NumberInput";
import { Segmented } from "./RangePicker";
import { TagColorPicker, useTagColors } from "./Tags";
import { useToast } from "./Toast";
import { classNames, latencyLabel, sortTags } from "../utils";

const DNS_RECORD_TYPES: DnsRecordType[] = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "PTR", "SRV"];
const GLOBALPING_HELP: Record<GlobalpingMeasurement, string> = {
  ping: "Round-trip time and loss as seen from the remote probe(s).",
  traceroute: "The path from the remote probe, one reply per hop; stored like a local MTR run (no jitter statistics).",
  mtr: "The full path from the remote probe with per-hop loss and jitter; stored like a local MTR run.",
  dns: "The probe resolves the name (optionally through a specific resolver) and reports the answers and the lookup time.",
  http: "The probe fetches a path on the target host and reports status, timing breakdown and the certificate.",
};

const DEFAULTS: TargetInput = {
  name: "",
  host: "",
  type: "mtr",
  options: {},
  description: "",
  tags: [],
  interval_sec: 300,
  count: 10,
  probe_interval: 1.0,
  protocol: "icmp",
  port: null,
  packet_size: 64,
  ip_version: "auto",
  max_hops: 30,
  enabled: true,
  notify: true,
  alert_loss_pct: 5,
  alert_latency_ms: 200,
};

/** "https://host:8443/path" -> "host"; anything that is not a URL is returned as typed. */
function hostFromUrl(value: string): string {
  const m = /^https?:\/\/([^/?#:]+)/i.exec(value.trim());
  return m ? m[1] : value;
}

/** Comma-separated text -> trimmed, de-duplicated tags (order as typed; sorted where displayed and stored). */
function parseTags(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(",")) {
    const t = raw.trim();
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

const INTERVAL_PRESETS: { label: string; value: number }[] = [
  { label: "30s", value: 30 },
  { label: "1 min", value: 60 },
  { label: "2 min", value: 120 },
  { label: "5 min", value: 300 },
  { label: "10 min", value: 600 },
  { label: "15 min", value: 900 },
  { label: "30 min", value: 1800 },
  { label: "1 h", value: 3600 },
];

export function TargetForm({
  open,
  initial,
  prefill,
  title,
  submitLabel,
  onClose,
  onSubmit,
  submitting,
}: {
  open: boolean;
  /** The target being edited; absent when adding (or cloning, where `prefill` carries the copy). */
  initial?: Target | null;
  /** Starting values for a new target: a host from the quick trace, or a full copy of a target being cloned. */
  prefill?: Partial<TargetInput> | null;
  title?: string;
  submitLabel?: string;
  onClose: () => void;
  onSubmit: (values: TargetInput) => Promise<void>;
  submitting: boolean;
}) {
  const [form, setForm] = useState<TargetInput>(DEFAULTS);
  const [tagText, setTagText] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [headersText, setHeadersText] = useState("");
  const toast = useToast();
  const { colors: tagColors, refresh: refreshTagColors } = useTagColors();
  // Colour choices made in this form: tag -> hex, or null for "back to automatic". Saved once the target is saved.
  const [colorDraft, setColorDraft] = useState<Record<string, string | null>>({});
  const parsedTags = useMemo(() => sortTags(parseTags(tagText)), [tagText]);

  // Initialise the fields when the form opens or when a different target is edited. The deps deliberately use
  // the target's id rather than the object: the target page re-fetches its target every few seconds and hands
  // the fresh object down, and re-initialising on every refresh wiped whatever was being typed.
  const initialId = initial?.id ?? null;
  useEffect(() => {
    if (!open) return;
    setColorDraft({});
    // Editing starts from the target; adding starts from the defaults plus any prefill (a quick-trace host, or a clone).
    const base: TargetInput = initial ? targetInput(initial) : { ...DEFAULTS, ...(prefill ?? {}) };
    const type = base.type || "mtr";
    setForm({ ...base, type, options: { ...DEFAULT_OPTIONS[type], ...(base.options || {}) } });
    setTagText(base.tags.join(", "));
    setAdvanced(base.protocol !== "icmp" || base.packet_size !== 64 || base.max_hops !== 30 || base.ip_version !== "auto" || base.probe_interval !== 1);
    setHeadersText(Object.entries(base.options?.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n"));
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialId, prefill]);

  const set = <K extends keyof TargetInput>(k: K, v: TargetInput[K]) => setForm((f) => ({ ...f, [k]: v }));
  const setOpt = <K extends keyof ProbeOptions>(k: K, v: ProbeOptions[K]) => setForm((f) => ({ ...f, options: { ...f.options, [k]: v } }));
  const setType = (type: ProbeType) =>
    setForm((f) => ({
      ...f,
      type,
      // A URL only makes sense for HTTP checks; other probe types resolve a plain host name.
      host: f.type === "http" && type !== "http" ? hostFromUrl(f.host) : f.host,
      options: { ...DEFAULT_OPTIONS[type] },
      port: type === "tcp" ? (f.port ?? 443) : f.port,
      // Sensible packet counts per type: Globalping allows at most 16 packets per probe.
      count: type === "ping" && f.count === 10 ? 5 : type === "globalping" && (f.count === 10 || f.count > 16) ? 4 : f.count,
      // Keep a user-edited threshold; only swap the per-type default.
      alert_latency_ms: f.alert_latency_ms === LATENCY_ALERT_DEFAULT[f.type] ? LATENCY_ALERT_DEFAULT[type] : f.alert_latency_ms,
    }));
  const isMtr = form.type === "mtr";
  const isPing = form.type === "ping";
  const isHttp = form.type === "http";
  const isTcp = form.type === "tcp";
  const isDns = form.type === "dns";
  const dnsTransport: DnsTransport = form.options.transport ?? "udp";
  const dnsEncrypted = dnsTransport === "dot" || dnsTransport === "doh";
  const isGlobalping = form.type === "globalping";
  const gpMeasurement = (form.options.measurement ?? "ping") as GlobalpingMeasurement;
  const gpPath = isGlobalping && (gpMeasurement === "mtr" || gpMeasurement === "traceroute"); // stored as hops
  const gpPackets = isGlobalping && (gpMeasurement === "ping" || gpMeasurement === "mtr"); // the API takes a packet count
  const gpPacket = isGlobalping && gpMeasurement !== "dns" && gpMeasurement !== "http"; // reports packet loss
  const usesProbes = isMtr || isPing || gpPacket;
  const showCount = isMtr || isPing || gpPackets;
  const alertWord = latencyLabel(form.type, form.options);
  // mtr sends one probe cycle per probe interval, then waits roughly 5 s for late replies; ping waits up to its
  // timeout; a Globalping measurement adds the API round trip and the probe's own schedule.
  const runDuration = isMtr
    ? Math.round(form.count * form.probe_interval + 5)
    : isPing
      ? Math.round(form.count * form.probe_interval + (form.options.timeout_sec ?? 2))
      : isGlobalping
        ? Math.round((gpPackets ? form.count : 3) + 15)
        : Math.round(form.options.timeout_sec ?? 10);
  const durationWarn = runDuration > form.interval_sec * 0.9;

  /** Tag colours are global settings, so they are written after the target itself has been saved. */
  const saveTagColors = async (tags: string[]) => {
    const changes = Object.entries(colorDraft).filter(([tag, hex]) => tags.includes(tag) && hex !== (tagColors[tag] ?? null));
    if (!changes.length) return;
    try {
      const next = { ...((await api.settings()).tag_colors ?? {}) };
      for (const [tag, hex] of changes) {
        if (hex) next[tag] = hex;
        else delete next[tag];
      }
      await api.updateSettings({ tag_colors: next });
      await refreshTagColors();
    } catch (e) {
      toast(`Target saved, but the tag colours were not: ${e instanceof Error ? e.message : String(e)}`, "error");
    }
  };

  const submit = async () => {
    setError(null);
    if (!form.name.trim()) return setError("Name is required.");
    if (!form.host.trim()) return setError("Host is required.");
    if (durationWarn) return setError(`A run takes about ${runDuration}s (probes × probe interval, plus mtr's final wait), which does not fit the ${form.interval_sec}s schedule. Increase the interval or lower the probe count.`);
    const tags = parsedTags;
    if (isTcp && !form.port) return setError("TCP probes need a port.");
    if (isDns && dnsEncrypted && !(form.options.resolver ?? "").trim()) return setError(`${DNS_TRANSPORT_LABEL[dnsTransport]} needs a resolver: the system resolver speaks plain DNS only.`);
    const options: ProbeOptions = { ...form.options };
    if (isHttp) {
      const headers: Record<string, string> = {};
      for (const line of headersText.split("\n")) {
        const idx = line.indexOf(":");
        if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        else if (line.trim()) return setError(`Header line "${line.trim()}" must look like Name: value.`);
      }
      options.headers = headers;
    }
    try {
      await onSubmit({ ...form, name: form.name.trim(), host: form.host.trim(), tags, options, port: isMtr && form.protocol === "icmp" ? null : form.port });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    await saveTagColors(tags);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title ?? (initial ? `Edit ${initial.name}` : "Add target")}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? "Saving…" : (submitLabel ?? (initial ? "Save changes" : "Add target"))}
          </button>
        </>
      }
    >
      <form
        className="grid grid-cols-1 gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="sm:col-span-2">
          <label className="label">Probe type</label>
          <div className="seg w-full" role="radiogroup">
            {(Object.keys(PROBE_TYPE_LABEL) as ProbeType[]).map((k) => (
              <button key={k} type="button" className="flex-1" data-active={form.type === k} onClick={() => setType(k)} role="radio" aria-checked={form.type === k}>
                {PROBE_TYPE_LABEL[k]}
              </button>
            ))}
          </div>
          <div className="help">
            {isMtr && "Full path trace: every hop, loss, latency and jitter. Detects route changes."}
            {isPing && "ICMP echo to the destination only. Lightweight; good for many targets on short intervals."}
            {isHttp && "Fetch a URL and check the status code, and optionally a keyword or a JSON value. Warns before the TLS certificate expires."}
            {isTcp && "Open a TCP connection to a port and measure connect time. Good for services that do not answer ping."}
            {isDns && "Resolve a name and check the answer, optionally against a specific resolver."}
            {isGlobalping && "Ping, traceroute, MTR, DNS or HTTP run from a remote Globalping probe (globalping.io): pick a country, city, network or ASN and see the result from there."}
          </div>
        </div>
        <div>
          <label className="label">Name</label>
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder={isHttp ? "Intranet portal" : "Head office WAN"} autoFocus />
        </div>
        <div>
          <label className="label">{isHttp ? "URL" : isDns || (isGlobalping && gpMeasurement === "dns") ? "Name to resolve" : "Host or IP"}</label>
          <input className="input font-mono" value={form.host} onChange={(e) => set("host", e.target.value)} placeholder={isHttp ? "https://portal.example.com/health" : isDns || (isGlobalping && gpMeasurement === "dns") ? "www.example.com" : isGlobalping && gpMeasurement === "http" ? "portal.example.com" : "1.1.1.1 or vpn.example.com"} spellCheck={false} />
        </div>
        {isTcp && (
          <div>
            <label className="label">Port</label>
            <NumberInput className="input num" min={1} max={65535} nullable value={form.port} onChange={(v) => set("port", v)} placeholder="443" />
          </div>
        )}
        {isGlobalping && (
          <>
            <div className="sm:col-span-2">
              <label className="label">Measurement</label>
              <Segmented<GlobalpingMeasurement>
                value={gpMeasurement}
                onChange={(v) => setOpt("measurement", v)}
                options={[{ value: "ping", label: "Ping" }, { value: "traceroute", label: "Traceroute" }, { value: "mtr", label: "MTR" }, { value: "dns", label: "DNS" }, { value: "http", label: "HTTP(S)" }]}
              />
              <div className="help">{GLOBALPING_HELP[gpMeasurement]}</div>
            </div>
            <div>
              <label className="label">Location</label>
              <input className="input font-mono" value={form.options.location ?? "world"} onChange={(e) => setOpt("location", e.target.value)} placeholder="world" spellCheck={false} />
              <div className="help">Country, city, continent, region, ASN, network or cloud region: Germany, Frankfurt, EU, AS3320, aws-eu-west-1. "world" picks any probe.</div>
            </div>
            {!gpPath && (
              <div>
                <label className="label">Probes at that location</label>
                <NumberInput className="input num" min={1} max={10} value={form.options.probes ?? 1} onChange={(v) => setOpt("probes", v ?? 1)} />
                <div className="help">{gpMeasurement === "ping" ? "Results are aggregated; every probe is listed in the run details." : "Every probe must pass for the target to be up; one failing probe makes it degraded."}</div>
              </div>
            )}
            {gpMeasurement === "dns" && (
              <>
                <div>
                  <label className="label">Record type</label>
                  <select className="input" value={form.options.record_type ?? "A"} onChange={(e) => setOpt("record_type", e.target.value as DnsRecordType)}>
                    {DNS_RECORD_TYPES.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
                <div>
                  <label className="label">Resolver (optional)</label>
                  <input className="input font-mono" value={form.options.resolver ?? ""} onChange={(e) => setOpt("resolver", e.target.value)} placeholder="the probe's own, or e.g. 1.1.1.1" spellCheck={false} />
                </div>
                <div>
                  <label className="label">Transport</label>
                  <select className="input" aria-label="DNS transport" value={form.options.dns_transport ?? "udp"} onChange={(e) => setOpt("dns_transport", e.target.value as "udp" | "tcp")}>
                    <option value="udp">{DNS_TRANSPORT_LABEL.udp}</option>
                    <option value="tcp">{DNS_TRANSPORT_LABEL.tcp}</option>
                  </select>
                  <div className="help">Globalping probes speak plain DNS only; use a local DNS check for DoT or DoH.</div>
                </div>
                <div className="sm:col-span-2">
                  <label className="label">Expected answer (optional)</label>
                  <input className="input font-mono" value={form.options.expected ?? ""} onChange={(e) => setOpt("expected", e.target.value)} placeholder="substring of an expected answer" spellCheck={false} />
                </div>
              </>
            )}
            {gpMeasurement === "http" && (
              <>
                <div>
                  <label className="label">Path</label>
                  <input className="input font-mono" value={form.options.path ?? "/"} onChange={(e) => setOpt("path", e.target.value)} placeholder="/health" spellCheck={false} />
                  <div className="help">Requested on the host above; a full URL pasted as host is split into host and path.</div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label">Method</label>
                    <select className="input" value={form.options.http_method ?? "GET"} onChange={(e) => setOpt("http_method", e.target.value as GlobalpingHttpMethod)}>
                      {["GET", "HEAD", "OPTIONS"].map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="label">Protocol</label>
                    <select className="input" value={form.options.http_protocol ?? "HTTPS"} onChange={(e) => setOpt("http_protocol", e.target.value as GlobalpingHttpProtocol)}>
                      {["HTTPS", "HTTP", "HTTP2"].map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="label">Expected status</label>
                  <input className="input font-mono" value={form.options.expected_status ?? "200-299"} onChange={(e) => setOpt("expected_status", e.target.value)} placeholder="200-299" spellCheck={false} />
                  <div className="help">Codes or ranges, comma separated: 200, 200-299, 200,301.</div>
                </div>
                <div>
                  <label className="label">Keyword (optional)</label>
                  <input className="input" value={form.options.keyword ?? ""} onChange={(e) => setOpt("keyword", e.target.value)} placeholder="text that must appear in the body" />
                  <div className="help">Checked against the first 10 kB of the body the probe returns.</div>
                </div>
              </>
            )}
          </>
        )}
        {isHttp && (
          <>
            <div>
              <label className="label">Method</label>
              <select className="input" value={form.options.method ?? "GET"} onChange={(e) => setOpt("method", e.target.value as HttpMethod)}>
                {["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Expected status</label>
              <input className="input font-mono" value={form.options.expected_status ?? "200-299"} onChange={(e) => setOpt("expected_status", e.target.value)} placeholder="200-299" spellCheck={false} />
              <div className="help">Codes or ranges, comma separated: 200, 200-299, 200,301.</div>
            </div>
            <div>
              <label className="label">Keyword (optional)</label>
              <input className="input" value={form.options.keyword ?? ""} onChange={(e) => setOpt("keyword", e.target.value)} placeholder="text that must appear in the body" />
              <label className="mt-1.5 flex items-center gap-2 text-xs text-muted">
                <input type="checkbox" checked={!!form.options.keyword_absent} onChange={(e) => setOpt("keyword_absent", e.target.checked)} /> Fail if the keyword is present instead
              </label>
            </div>
            <div>
              <label className="label">JSON check (optional)</label>
              <div className="flex gap-2">
                <input className="input font-mono" value={form.options.json_path ?? ""} onChange={(e) => setOpt("json_path", e.target.value)} placeholder="data.status" spellCheck={false} />
                <input className="input font-mono w-40" value={form.options.json_expected ?? ""} onChange={(e) => setOpt("json_expected", e.target.value)} placeholder="ok" spellCheck={false} />
              </div>
              <div className="help">Path like items[0].state; expected value, or a comparison such as {">= 5"}, or ~substring.</div>
            </div>
            <div>
              <label className="label">Timeout (s)</label>
              <NumberInput className="input num" min={1} max={120} value={form.options.timeout_sec ?? 10} onChange={(v) => setOpt("timeout_sec", v ?? 10)} />
            </div>
            <div>
              <label className="label">Warn when certificate expires within (days)</label>
              <NumberInput className="input num" min={0} max={365} value={form.options.tls_warn_days ?? 14} onChange={(v) => setOpt("tls_warn_days", v ?? 14)} />
              <div className="help">0 disables. Marks the target degraded.</div>
            </div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.options.verify_tls ?? true} onChange={(e) => setOpt("verify_tls", e.target.checked)} /> Verify TLS certificate</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.options.follow_redirects ?? true} onChange={(e) => setOpt("follow_redirects", e.target.checked)} /> Follow redirects</label>
            <label className="flex items-start gap-2 text-sm sm:col-span-2">
              <input type="checkbox" className="mt-0.5" checked={form.options.tls_info ?? true} onChange={(e) => setOpt("tls_info", e.target.checked)} />
              <span>
                Record certificate details
                <div className="help">Issuer, subject, validity period, alternative names and the negotiated protocol are shown with each run (verified HTTPS connections only).</div>
              </span>
            </label>
            <div className="sm:col-span-2">
              <label className="label">Headers (optional, one per line as Name: value)</label>
              <textarea className="input font-mono" rows={2} value={headersText} onChange={(e) => setHeadersText(e.target.value)} placeholder={"Authorization: Bearer …\nAccept: application/json"} spellCheck={false} />
            </div>
            {["POST", "PUT", "PATCH"].includes(form.options.method ?? "GET") && (
              <div className="sm:col-span-2">
                <label className="label">Request body (optional)</label>
                <textarea className="input font-mono" rows={3} value={form.options.body ?? ""} onChange={(e) => setOpt("body", e.target.value)} spellCheck={false} />
              </div>
            )}
          </>
        )}
        {isDns && (
          <>
            <div>
              <label className="label">Record type</label>
              <select className="input" value={form.options.record_type ?? "A"} onChange={(e) => setOpt("record_type", e.target.value as DnsRecordType)}>
                {["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "PTR", "SRV"].map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Transport</label>
              <select className="input" aria-label="DNS transport" value={dnsTransport} onChange={(e) => setOpt("transport", e.target.value as DnsTransport)}>
                {(Object.keys(DNS_TRANSPORT_LABEL) as DnsTransport[]).map((k) => <option key={k} value={k}>{DNS_TRANSPORT_LABEL[k]}</option>)}
              </select>
            </div>
            <div>
              <label className="label">{dnsEncrypted ? "Resolver" : "Resolver (optional)"}</label>
              <input className="input font-mono" aria-label="Resolver" value={form.options.resolver ?? ""} onChange={(e) => setOpt("resolver", e.target.value)} placeholder={dnsTransport === "doh" ? "https://dns.google/dns-query or dns.google" : dnsTransport === "dot" ? "dns.google or 1.1.1.1" : "system resolver, or e.g. 1.1.1.1"} spellCheck={false} />
              {dnsTransport === "doh" && <div className="help">A host name or IP gets the standard /dns-query path; paste a full URL for any other path or port.</div>}
            </div>
            {dnsTransport !== "doh" && (
              <div>
                <label className="label">Resolver port (optional)</label>
                <NumberInput className="input num" aria-label="Resolver port" min={1} max={65535} nullable value={form.options.resolver_port ?? null} onChange={(v) => setOpt("resolver_port", v)} placeholder={dnsTransport === "dot" ? "853" : "53"} />
              </div>
            )}
            {dnsEncrypted && (
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-0.5" checked={form.options.verify_tls ?? true} onChange={(e) => setOpt("verify_tls", e.target.checked)} />
                <span>
                  Verify the resolver's certificate
                  <div className="help">Checked against the resolver's host name, or its IP when you give an address. Turn off only for internal resolvers with private certificates.</div>
                </span>
              </label>
            )}
            <div>
              <label className="label">Expected answer (optional)</label>
              <input className="input font-mono" value={form.options.expected ?? ""} onChange={(e) => setOpt("expected", e.target.value)} placeholder="substring of an expected answer" spellCheck={false} />
            </div>
            <div>
              <label className="label">Timeout (s)</label>
              <NumberInput className="input num" min={0.5} max={60} step={0.5} value={form.options.timeout_sec ?? 5} onChange={(v) => setOpt("timeout_sec", v ?? 5)} />
            </div>
            <label className="flex items-start gap-2 text-sm sm:col-span-2">
              <input type="checkbox" className="mt-0.5" checked={!!form.options.random_prefix} onChange={(e) => setOpt("random_prefix", e.target.checked)} />
              <span>
                Query a random subdomain each run (uncached lookup time)
                <div className="help">A random label under the name is looked up, so no resolver can answer from its cache and the time reflects a real recursive lookup. NXDOMAIN counts as success; the expected-answer check then only makes sense for zones with a wildcard.</div>
              </span>
            </label>
          </>
        )}
        {(isTcp || isPing) && (
          <div>
            <label className="label">Timeout per {isPing ? "probe" : "connect"} (s)</label>
            <NumberInput className="input num" min={0.2} max={60} step={0.5} value={form.options.timeout_sec ?? (isPing ? 2 : 5)} onChange={(v) => setOpt("timeout_sec", v ?? (isPing ? 2 : 5))} />
          </div>
        )}
        <div className="sm:col-span-2">
          <label className="label">Description (optional)</label>
          <input className="input" value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="What this target represents and why it matters" />
        </div>
        <div className="sm:col-span-2">
          <label className="label">Tags (comma separated)</label>
          <input className="input" value={tagText} onChange={(e) => setTagText(e.target.value)} placeholder="wan, isp-a, critical" />
          {parsedTags.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {parsedTags.map((tag) => (
                <TagColorPicker key={tag} tag={tag} value={colorDraft[tag] !== undefined ? colorDraft[tag] : (tagColors[tag] ?? null)} onChange={(hex) => setColorDraft((d) => ({ ...d, [tag]: hex }))} />
              ))}
              <span className="text-[11px] text-faint">Sorted alphabetically. Click a tag to pick its colour; it applies wherever the tag is used.</span>
            </div>
          )}
        </div>

        <div>
          <label className="label">Run every</label>
          <div className="flex gap-2">
            <select className="input" value={INTERVAL_PRESETS.some((p) => p.value === form.interval_sec) ? form.interval_sec : "custom"} onChange={(e) => e.target.value !== "custom" && set("interval_sec", Number(e.target.value))}>
              {INTERVAL_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
              <option value="custom">Custom…</option>
            </select>
            <NumberInput className="input w-28 num" min={10} max={86400} value={form.interval_sec} onChange={(v) => set("interval_sec", v ?? form.interval_sec)} aria-label="Interval in seconds" />
          </div>
          <div className="help">Seconds between the start of one run and the start of the next (10 – 86400).</div>
        </div>
        {showCount ? (
          <div>
            <label className="label">{isMtr ? "Probes per hop" : isGlobalping ? "Packets per probe" : "Pings per run"}</label>
            <NumberInput className="input num" min={1} max={isGlobalping ? 16 : 200} value={form.count} onChange={(v) => set("count", v ?? form.count)} />
            <div className={classNames("help", durationWarn && "!text-degraded")}>
              {isMtr
                ? `Each run sends ${form.count} probes per hop and takes about ${runDuration}s including mtr's final wait.`
                : isGlobalping
                  ? `Each run sends ${form.count} packets from the remote probe (max 16) and takes about ${runDuration}s including the API round trip.`
                  : `Each run sends ${form.count} pings and takes about ${runDuration}s.`}
            </div>
          </div>
        ) : (
          <div className="hidden sm:block" />
        )}

        {usesProbes ? (
          <div>
            <label className="label">Alert when packet loss ≥ (%)</label>
            <NumberInput className="input num" min={0} max={100} step={0.5} value={form.alert_loss_pct} onChange={(v) => set("alert_loss_pct", v ?? form.alert_loss_pct)} />
            <div className="help">0 disables the loss alert.</div>
          </div>
        ) : (
          <div className="hidden sm:block" />
        )}
        <div>
          <label className="label">Alert when {alertWord === "Latency" ? "avg latency" : `${alertWord.toLowerCase()} time`} ≥ (ms)</label>
          <NumberInput className="input num" min={0} step={1} value={form.alert_latency_ms} onChange={(v) => set("alert_latency_ms", v ?? form.alert_latency_ms)} />
          <div className="help">0 disables the latency alert.</div>
        </div>

        {usesProbes && (
          <div className="sm:col-span-2">
            <button type="button" className="text-xs font-medium text-accent hover:underline" onClick={() => setAdvanced((a) => !a)}>
              {advanced ? "Hide" : "Show"} advanced probe options
            </button>
          </div>
        )}
        {usesProbes && advanced && (
          <>
            {(isMtr || gpPath) && (
              <>
                <div>
                  <label className="label">Protocol</label>
                  <select className="input" value={form.protocol} onChange={(e) => set("protocol", e.target.value as Protocol)}>
                    <option value="icmp">ICMP echo</option>
                    <option value="udp">UDP</option>
                    <option value="tcp">TCP SYN</option>
                  </select>
                </div>
                <div>
                  <label className="label">Port {form.protocol === "icmp" && "(UDP/TCP only)"}</label>
                  <NumberInput className="input num" min={1} max={65535} nullable disabled={form.protocol === "icmp"} value={form.port} onChange={(v) => set("port", v)} placeholder={form.protocol === "tcp" ? "443" : "33434"} />
                </div>
              </>
            )}
            <div>
              <label className="label">IP version</label>
              <select className="input" value={form.ip_version} onChange={(e) => set("ip_version", e.target.value as IpVersion)}>
                <option value="auto">Auto (prefer IPv4)</option>
                <option value="4">IPv4 only</option>
                <option value="6">IPv6 only</option>
              </select>
            </div>
            {(isMtr || isPing) && (
              <>
                <div>
                  <label className="label">Probe interval (s)</label>
                  <NumberInput className="input num" min={0.1} max={10} step={0.1} value={form.probe_interval} onChange={(v) => set("probe_interval", v ?? form.probe_interval)} />
                  <div className="help">Delay between probes ({isMtr ? "mtr -i" : "ping -i"}).{isMtr ? " Below 1 s only works when mtr runs as root; otherwise the server uses 1 s." : ""}</div>
                </div>
                <div>
                  <label className="label">Packet size (bytes)</label>
                  <NumberInput className="input num" min={28} max={1500} value={form.packet_size} onChange={(v) => set("packet_size", v ?? form.packet_size)} />
                </div>
              </>
            )}
            {isMtr && (
              <div>
                <label className="label">Max hops</label>
                <NumberInput className="input num" min={1} max={64} value={form.max_hops} onChange={(v) => set("max_hops", v ?? form.max_hops)} />
              </div>
            )}
          </>
        )}

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Monitoring enabled
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={form.notify ?? true} onChange={(e) => set("notify", e.target.checked)} />
          <span>
            Send notifications
            <div className="help">Deliver this target's down, recovered, degraded and route change events to the channels enabled under Settings. Events are recorded either way.</div>
          </span>
        </label>

        {error && (
          <div className="sm:col-span-2 rounded-lg px-3 py-2 text-sm" style={{ background: "var(--down-soft)", color: "var(--down)" }}>
            {error}
          </div>
        )}
      </form>
    </Modal>
  );
}
