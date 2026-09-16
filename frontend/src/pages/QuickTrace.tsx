import { useState } from "react";
import { Radar, Plus } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, type IpVersion, type ProbeResult, type Protocol } from "../api";
import { HopTable } from "../components/HopTable";
import { ErrorBanner } from "../components/EmptyState";

export function QuickTrace() {
  const navigate = useNavigate();
  const [host, setHost] = useState("");
  const [count, setCount] = useState(5);
  const [protocol, setProtocol] = useState<Protocol>("icmp");
  const [port, setPort] = useState<number | null>(null);
  const [ipVersion, setIpVersion] = useState<IpVersion>("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProbeResult | null>(null);

  const run = async () => {
    if (!host.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.probe({ host: host.trim(), count, protocol, port: protocol === "icmp" ? null : port, ip_version: ipVersion, max_hops: 30 }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Quick trace</h1>
        <p className="text-sm text-muted">Run a one-off MTR from this server without saving anything. Useful for troubleshooting or before adding a target.</p>
      </div>
      <form className="card grid grid-cols-2 gap-3 p-4 md:grid-cols-6" onSubmit={(e) => { e.preventDefault(); void run(); }}>
        <div className="col-span-2">
          <label className="label">Host or IP</label>
          <input className="input font-mono" value={host} onChange={(e) => setHost(e.target.value)} placeholder="example.com" autoFocus spellCheck={false} />
        </div>
        <div>
          <label className="label">Probes</label>
          <input className="input num" type="number" min={1} max={50} value={count} onChange={(e) => setCount(Number(e.target.value))} />
        </div>
        <div>
          <label className="label">Protocol</label>
          <select className="input" value={protocol} onChange={(e) => setProtocol(e.target.value as Protocol)}>
            <option value="icmp">ICMP</option>
            <option value="udp">UDP</option>
            <option value="tcp">TCP</option>
          </select>
        </div>
        <div>
          <label className="label">Port</label>
          <input className="input num" type="number" disabled={protocol === "icmp"} value={port ?? ""} onChange={(e) => setPort(e.target.value ? Number(e.target.value) : null)} placeholder={protocol === "tcp" ? "443" : "33434"} />
        </div>
        <div>
          <label className="label">IP</label>
          <select className="input" value={ipVersion} onChange={(e) => setIpVersion(e.target.value as IpVersion)}>
            <option value="auto">Auto</option>
            <option value="4">IPv4</option>
            <option value="6">IPv6</option>
          </select>
        </div>
        <div className="col-span-2 flex items-end gap-2 md:col-span-6">
          <button className="btn btn-primary" type="submit" disabled={busy || !host.trim()}><Radar size={15} className={busy ? "animate-pulse" : ""} /> {busy ? `Tracing… (~${Math.ceil(count * 0.5 + 2)}s)` : "Trace"}</button>
          {result && (
            <button type="button" className="btn" onClick={() => navigate("/", { state: { prefillHost: result.host } })}><Plus size={15} /> Add as target</button>
          )}
        </div>
      </form>
      {error && <ErrorBanner message={error} />}
      {result && (
        <div className="card overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5 text-sm">
            <div>
              <span className="font-semibold">{result.host}</span>
              <span className="ml-2 font-mono text-muted">{result.dst_ip}</span>
              <span className="ml-3 text-xs font-semibold" style={{ color: result.reached ? "var(--up)" : "var(--down)" }}>{result.reached ? "destination reached" : "destination did not respond"}</span>
            </div>
            <div className="text-xs text-faint">{result.hops.length} hops · {(result.duration_ms / 1000).toFixed(1)}s{result.src ? ` · from ${result.src}` : ""}</div>
          </div>
          <HopTable hops={result.hops} dstIp={result.dst_ip} />
          <div className="border-t border-border px-4 py-2 font-mono text-[11px] text-faint">{result.command}</div>
        </div>
      )}
    </div>
  );
}
