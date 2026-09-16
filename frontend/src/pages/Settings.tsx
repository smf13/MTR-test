import { useEffect, useState } from "react";
import { Save, Database, Cpu, FlaskConical, Webhook } from "lucide-react";
import { api, type Settings as SettingsT } from "../api";
import { usePoll } from "../hooks";
import { useToast } from "../components/Toast";
import { ErrorBanner } from "../components/EmptyState";
import { fmtBytes, fmtDuration } from "../utils";

const EVENT_OPTIONS = [
  { value: "down", label: "Target down" },
  { value: "recovered", label: "Target recovered" },
  { value: "degraded", label: "Target degraded (threshold)" },
  { value: "route_change", label: "Route change" },
];

export function Settings() {
  const toast = useToast();
  const settings = usePoll(() => api.settings(), 60000);
  const status = usePoll(() => api.status(), 10000);
  const [form, setForm] = useState<SettingsT | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (settings.data && !form) setForm(settings.data);
  }, [settings.data, form]);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const updated = await api.updateSettings(form);
      setForm(updated);
      toast("Settings saved", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setSaving(false);
    }
  };

  const s = status.data;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted">Global behaviour of the monitoring engine. Per-target probe options live on each target.</p>
      </div>
      {settings.error && <ErrorBanner message={settings.error} />}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          {form && (
            <>
              <section className="card p-5">
                <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Database size={15} /> Data & lookups</h2>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="label">Retention (days)</label>
                    <input className="input num" type="number" min={1} max={3650} value={form.retention_days} onChange={(e) => setForm({ ...form, retention_days: Number(e.target.value) })} />
                    <div className="help">Runs, hops and events older than this are purged hourly.</div>
                  </div>
                  <div>
                    <label className="label">Site name</label>
                    <input className="input" value={form.site_name} onChange={(e) => setForm({ ...form, site_name: e.target.value })} />
                    <div className="help">Used as the source name in webhook payloads.</div>
                  </div>
                  <label className="flex items-start gap-2 text-sm">
                    <input type="checkbox" className="mt-0.5" checked={form.reverse_dns} onChange={(e) => setForm({ ...form, reverse_dns: e.target.checked })} />
                    <span>
                      Reverse DNS for hops
                      <div className="help">Resolves hop IPs to hostnames after each run (cached for an hour).</div>
                    </span>
                  </label>
                  <label className="flex items-start gap-2 text-sm">
                    <input type="checkbox" className="mt-0.5" checked={form.asn_lookup} onChange={(e) => setForm({ ...form, asn_lookup: e.target.checked })} />
                    <span>
                      ASN lookup
                      <div className="help">Passes -z to mtr to annotate hops with their autonomous system. Requires outbound DNS.</div>
                    </span>
                  </label>
                </div>
              </section>

              <section className="card p-5">
                <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Webhook size={15} /> Notifications</h2>
                <div className="space-y-4">
                  <div>
                    <label className="label">Webhook URL</label>
                    <input className="input font-mono" placeholder="https://hooks.example.com/mtr-tracker" value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} spellCheck={false} />
                    <div className="help">A JSON POST is sent for the selected events. Works with any generic webhook receiver (n8n, Zapier, custom).</div>
                  </div>
                  <div>
                    <label className="label">Send for</label>
                    <div className="flex flex-wrap gap-x-5 gap-y-2">
                      {EVENT_OPTIONS.map((o) => (
                        <label key={o.value} className="flex items-center gap-2 text-sm">
                          <input type="checkbox" checked={form.webhook_events.includes(o.value)} onChange={(e) => setForm({ ...form, webhook_events: e.target.checked ? [...form.webhook_events, o.value] : form.webhook_events.filter((x) => x !== o.value) })} />
                          {o.label}
                        </label>
                      ))}
                    </div>
                  </div>
                  <details className="text-xs text-muted">
                    <summary className="cursor-pointer font-medium">Payload example</summary>
                    <pre className="mt-2 overflow-x-auto rounded-lg border border-border p-3 font-mono text-[11px]" style={{ background: "var(--bg-elev)" }}>{`{
  "source": "MTR Tracker",
  "event": "down",
  "severity": "critical",
  "message": "Head office WAN is DOWN: destination unreachable",
  "target": { "id": 3, "name": "Head office WAN", "host": "203.0.113.1" },
  "run_id": 1842,
  "details": { "previous": "up", "current": "down", "loss_pct": 100.0 },
  "timestamp": 1758000000.0
}`}</pre>
                  </details>
                </div>
              </section>

              <div className="flex justify-end">
                <button className="btn btn-primary" onClick={save} disabled={saving}><Save size={15} /> {saving ? "Saving…" : "Save settings"}</button>
              </div>
            </>
          )}
        </div>

        <aside className="space-y-5">
          <section className="card p-5">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Cpu size={15} /> Engine</h2>
            {s ? (
              <dl className="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-muted">Version</dt><dd className="text-right">{s.version}</dd>
                <dt className="text-muted">Uptime</dt><dd className="text-right">{fmtDuration(s.uptime_sec)}</dd>
                <dt className="text-muted">mtr</dt><dd className="text-right font-mono text-xs">{s.mtr_version ?? `not found (${s.mtr_binary})`}</dd>
                <dt className="text-muted">Mode</dt><dd className="text-right">{s.simulate ? <span className="inline-flex items-center gap-1 text-degraded"><FlaskConical size={13} /> simulation</span> : "live probes"}</dd>
                <dt className="text-muted">Concurrency</dt><dd className="text-right">{s.max_concurrent_runs} runs</dd>
                <dt className="text-muted">Active now</dt><dd className="text-right">{s.active_runs.length}</dd>
                <dt className="text-muted">Runs since start</dt><dd className="text-right">{s.runs_completed_since_start}</dd>
                <dt className="text-muted">Runs stored</dt><dd className="text-right">{s.runs_total.toLocaleString()}</dd>
                <dt className="text-muted">Database</dt><dd className="text-right">{fmtBytes(s.db_size_bytes)}</dd>
                <dt className="text-muted">DB path</dt><dd className="truncate text-right font-mono text-[11px]" title={s.db_path}>{s.db_path}</dd>
              </dl>
            ) : (
              <div className="text-sm text-faint">Loading…</div>
            )}
            {s?.simulate && <p className="mt-3 text-xs text-degraded">Simulation mode is on: paths are synthetic. Unset MTR_TRACKER_SIMULATE to send real probes.</p>}
            {s && !s.simulate && !s.mtr_version && <p className="mt-3 text-xs text-down">The mtr binary was not found. Install mtr / mtr-tiny or set MTR_TRACKER_MTR_BINARY.</p>}
          </section>
          <section className="card p-5 text-xs text-muted">
            <h2 className="mb-2 text-sm font-semibold text-text">Environment variables</h2>
            <ul className="space-y-1 font-mono">
              <li>MTR_TRACKER_PORT=8899</li>
              <li>MTR_TRACKER_DATA_DIR=/data</li>
              <li>MTR_TRACKER_MAX_CONCURRENT_RUNS=4</li>
              <li>MTR_TRACKER_MTR_BINARY=mtr</li>
              <li>MTR_TRACKER_SIMULATE=0</li>
            </ul>
            <p className="mt-2 font-sans">These are read at startup; restart the container to apply changes.</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
