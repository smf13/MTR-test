import { useEffect, useMemo, useRef, useState } from "react";
import { Save, Database, Cpu, FlaskConical, Webhook, BellRing, Send, KeyRound, Download, Upload, Tags as TagsIcon, Globe, MapPin, CloudDownload } from "lucide-react";
import { api, getApiToken, setApiToken, type Settings as SettingsT, type TargetInput } from "../api";
import { useNow, usePoll } from "../hooks";
import { useToast } from "../components/Toast";
import { ErrorBanner } from "../components/EmptyState";
import { NumberInput } from "../components/NumberInput";
import { TagColorPicker, useTagColors } from "../components/Tags";
import { fmtBytes, fmtDuration, relTime, sortTags } from "../utils";

/** Copy of a tag colour map with one tag set (hex) or reset to automatic (null). */
function withTagColor(map: Record<string, string>, tag: string, hex: string | null): Record<string, string> {
  const next = { ...map };
  if (hex) next[tag] = hex;
  else delete next[tag];
  return next;
}

const EVENT_OPTIONS = [
  { value: "down", label: "Target down" },
  { value: "recovered", label: "Target recovered" },
  { value: "degraded", label: "Target degraded (threshold)" },
  { value: "route_change", label: "Route change" },
];

export function Settings() {
  const toast = useToast();
  const now = useNow(5000);
  const settings = usePoll(() => api.settings(), 60000);
  const status = usePoll(() => api.status(), 10000);
  const tags = usePoll(() => api.tags(), 60000);
  const geoip = usePoll(() => api.geoipStatus(), 15000);
  const [downloading, setDownloading] = useState(false);
  const { refresh: refreshTagColors } = useTagColors();
  const [form, setForm] = useState<SettingsT | null>(null);
  // Tags in use (with counts) plus any tag that only has a stored colour left over, so it can be reset.
  const tagRows = useMemo(() => {
    const counts = new Map<string, number>((tags.data ?? []).map((t) => [t.name, t.count]));
    Object.keys(form?.tag_colors ?? {}).forEach((name) => {
      if (!counts.has(name)) counts.set(name, 0);
    });
    return sortTags([...counts.keys()]).map((name) => ({ name, count: counts.get(name) ?? 0 }));
  }, [tags.data, form?.tag_colors]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<"webhook" | "pushover" | null>(null);
  const [token, setToken] = useState(getApiToken());
  const [importMode, setImportMode] = useState<"upsert" | "create" | "replace">("upsert");
  const fileRef = useRef<HTMLInputElement>(null);

  const exportTargets = async () => {
    try {
      const data = await api.exportTargets();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `mtr-tracker-targets-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const importFile = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text()) as TargetInput[] | { targets: TargetInput[] };
      const targets = Array.isArray(parsed) ? parsed : parsed.targets;
      if (!Array.isArray(targets) || !targets.length) throw new Error("File must contain a JSON array of targets.");
      if (importMode === "replace" && !window.confirm(`Replace ALL existing targets with the ${targets.length} in this file? Their run history will be deleted.`)) return;
      const res = await api.importTargets(targets, importMode);
      toast(`Imported: ${res.created} created, ${res.updated} updated (${res.total} total)`, "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const sendTest = async (channel: "webhook" | "pushover") => {
    if (!form) return;
    setTesting(channel);
    try {
      await api.testNotification(channel, form);
      toast(channel === "pushover" ? "Test push sent. Check your device." : "Test webhook delivered.", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setTesting(null);
    }
  };

  const downloadGeoIp = async () => {
    setDownloading(true);
    try {
      const st = await api.geoipUpdate();
      toast(st.build_epoch ? `GeoLite2 City database updated (build ${st.build_epoch.slice(0, 10)})` : "GeoLite2 City database updated", "success");
      void geoip.refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
      void geoip.refresh();
    } finally {
      setDownloading(false);
    }
  };

  const toggleEvent = (key: "webhook_events" | "pushover_events", value: string, on: boolean) => {
    if (!form) return;
    const list = form[key];
    setForm({ ...form, [key]: on ? [...list, value] : list.filter((x) => x !== value) });
  };

  useEffect(() => {
    if (settings.data && !form) setForm(settings.data);
  }, [settings.data, form]);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const updated = await api.updateSettings(form);
      setForm(updated);
      void refreshTagColors();
      void geoip.refresh();
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
                    <NumberInput className="input num" min={1} max={3650} value={form.retention_days} onChange={(v) => setForm({ ...form, retention_days: v ?? form.retention_days })} />
                    <div className="help">Runs, hops and events older than this are purged hourly.</div>
                  </div>
                  <div>
                    <label className="label">Site name</label>
                    <input className="input" value={form.site_name} onChange={(e) => setForm({ ...form, site_name: e.target.value })} />
                    <div className="help">Shown as the source in webhook payloads and Pushover titles.</div>
                  </div>
                  <div className="sm:col-span-2">
                    <label className="label">Public URL (optional)</label>
                    <input className="input font-mono" placeholder="https://mtr.example.com" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} spellCheck={false} />
                    <div className="help">Where this UI is reachable from your phone or team. Used to add an "open target" link to notifications.</div>
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
                <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold"><TagsIcon size={15} /> Tag colours</h2>
                <p className="mb-3 text-xs text-faint">Tags are sorted alphabetically everywhere. Every tag gets an automatic colour; click one to choose a preset or a custom colour. A colour applies to every target that carries the tag and is stored with these settings.</p>
                {tagRows.length ? (
                  <div className="flex flex-wrap items-center gap-2">
                    {tagRows.map((t) => (
                      <TagColorPicker key={t.name} tag={t.name} count={t.count} value={form.tag_colors[t.name] ?? null} onChange={(hex) => setForm((f) => f && { ...f, tag_colors: withTagColor(f.tag_colors, t.name, hex) })} />
                    ))}
                  </div>
                ) : (
                  <div className="text-sm text-faint">No tags yet. Tags added to targets appear here.</div>
                )}
              </section>

              <section className="card p-5">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h2 className="flex items-center gap-2 text-sm font-semibold"><Webhook size={15} /> Webhook</h2>
                  <button className="btn btn-sm" onClick={() => sendTest("webhook")} disabled={testing !== null || !form.webhook_url.trim()} title="Send a test payload using the values in this form (unsaved changes included)">
                    <Send size={13} /> {testing === "webhook" ? "Sending…" : "Send test"}
                  </button>
                </div>
                <div className="space-y-4">
                  <div>
                    <label className="label">Webhook URL</label>
                    <input className="input font-mono" placeholder="https://hooks.example.com/mtr-tracker" value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} spellCheck={false} />
                    <div className="help">A JSON POST is sent for the selected events. Works with any generic webhook receiver (n8n, Zapier, custom). Leave empty to disable.</div>
                  </div>
                  <div>
                    <label className="label">Send for</label>
                    <div className="flex flex-wrap gap-x-5 gap-y-2">
                      {EVENT_OPTIONS.map((o) => (
                        <label key={o.value} className="flex items-center gap-2 text-sm">
                          <input type="checkbox" checked={form.webhook_events.includes(o.value)} onChange={(e) => toggleEvent("webhook_events", o.value, e.target.checked)} />
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
  "url": "https://mtr.example.com/targets/3",
  "timestamp": 1758000000.0
}`}</pre>
                  </details>
                </div>
              </section>

              <section className="card p-5">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h2 className="flex items-center gap-2 text-sm font-semibold"><BellRing size={15} /> Pushover</h2>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={form.pushover_enabled} onChange={(e) => setForm({ ...form, pushover_enabled: e.target.checked })} />
                      Enabled
                    </label>
                    <button className="btn btn-sm" onClick={() => sendTest("pushover")} disabled={testing !== null || !form.pushover_api_token.trim() || !form.pushover_user_key.trim()} title="Send a test push using the values in this form (unsaved changes included)">
                      <Send size={13} /> {testing === "pushover" ? "Sending…" : "Send test"}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="label">User or group key</label>
                    <input className="input font-mono" value={form.pushover_user_key} onChange={(e) => setForm({ ...form, pushover_user_key: e.target.value })} spellCheck={false} autoComplete="off" placeholder="u…" />
                    <div className="help">From your Pushover dashboard. A delivery group key also works.</div>
                  </div>
                  <div>
                    <label className="label">Application API token</label>
                    <input className="input font-mono" type="password" value={form.pushover_api_token} onChange={(e) => setForm({ ...form, pushover_api_token: e.target.value })} spellCheck={false} autoComplete="new-password" placeholder="a…" />
                    <div className="help">Create an application at pushover.net/apps/build and paste its token.</div>
                  </div>
                  <div>
                    <label className="label">Device (optional)</label>
                    <input className="input font-mono" value={form.pushover_device} onChange={(e) => setForm({ ...form, pushover_device: e.target.value })} spellCheck={false} placeholder="all devices" />
                  </div>
                  <div>
                    <label className="label">Sound (optional)</label>
                    <input className="input font-mono" value={form.pushover_sound} onChange={(e) => setForm({ ...form, pushover_sound: e.target.value })} spellCheck={false} placeholder="pushover (default)" />
                    <div className="help">Any Pushover sound name, e.g. siren, alien, none.</div>
                  </div>
                  <div>
                    <label className="label">Priority</label>
                    <select className="input" value={form.pushover_priority} onChange={(e) => setForm({ ...form, pushover_priority: e.target.value as SettingsT["pushover_priority"] })}>
                      <option value="auto">Auto (by severity)</option>
                      <option value="-2">Lowest (silent)</option>
                      <option value="-1">Low (no sound)</option>
                      <option value="0">Normal</option>
                      <option value="1">High (bypass quiet hours)</option>
                      <option value="2">Emergency (repeats until acknowledged)</option>
                    </select>
                    <div className="help">Auto: down is high, degraded and recovered are normal, route change is low.</div>
                  </div>
                  <div className="sm:col-span-2">
                    <label className="label">Send for</label>
                    <div className="flex flex-wrap gap-x-5 gap-y-2">
                      {EVENT_OPTIONS.map((o) => (
                        <label key={o.value} className="flex items-center gap-2 text-sm">
                          <input type="checkbox" checked={form.pushover_events.includes(o.value)} onChange={(e) => toggleEvent("pushover_events", o.value, e.target.checked)} />
                          {o.label}
                        </label>
                      ))}
                    </div>
                    <div className="help">Route changes can be noisy on paths with load balancing; they are off by default.</div>
                  </div>
                </div>
              </section>

              <section className="card p-5">
                <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold"><Globe size={15} /> Globalping</h2>
                <p className="mb-3 text-xs text-faint">Targets of type Globalping run ping or MTR from remote probes of the globalping.io network. Without a token the API allows 250 measurements per hour per address; a free token from globalping.io raises the limit.</p>
                <label className="label">API token (optional)</label>
                <input className="input font-mono" type="password" value={form.globalping_token ?? ""} onChange={(e) => setForm({ ...form, globalping_token: e.target.value })} spellCheck={false} autoComplete="new-password" placeholder="leave empty for anonymous use" />
                <div className="help">Sent as a bearer token with every Globalping request. Shown masked to readers without the API token of this server.</div>
              </section>

              <section className="card p-5">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <h2 className="flex items-center gap-2 text-sm font-semibold"><MapPin size={15} /> MaxMind GeoIP</h2>
                  <button className="btn btn-sm" onClick={downloadGeoIp} disabled={downloading || !geoip.data?.configured || geoip.data?.updating || geoip.data?.simulated} title="Fetch the GeoLite2 City database now with the saved credentials">
                    <CloudDownload size={13} /> {downloading || geoip.data?.updating ? "Downloading…" : "Download now"}
                  </button>
                </div>
                <p className="mb-3 text-xs text-faint">A free GeoLite2 account at maxmind.com provides a licence key. Once it is saved, the server downloads the GeoLite2 City database into its data directory, refreshes it weekly, and every target page gains a map of the monitor, the hops and the destination. The database is fetched with your key and never bundled.</p>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="label">Licence key</label>
                    <input className="input font-mono" type="password" value={form.maxmind_license_key ?? ""} onChange={(e) => setForm({ ...form, maxmind_license_key: e.target.value })} spellCheck={false} autoComplete="new-password" placeholder="leave empty to disable the map" aria-label="MaxMind licence key" />
                    <div className="help">From <span className="font-mono">Account → Manage License Keys</span> on maxmind.com. Shown masked to readers without the API token of this server.</div>
                  </div>
                  <div>
                    <label className="label">Account ID (optional)</label>
                    <input className="input font-mono" value={form.maxmind_account_id ?? ""} onChange={(e) => setForm({ ...form, maxmind_account_id: e.target.value })} spellCheck={false} inputMode="numeric" placeholder="123456" aria-label="MaxMind account ID" />
                    <div className="help">With the account ID the download uses MaxMind's current authenticated endpoint; without it the key alone is used.</div>
                  </div>
                </div>
                <div className="mt-3 text-xs text-muted" aria-live="polite">
                  {geoip.data ? (
                    geoip.data.simulated ? (
                      <span>Simulation mode: locations are synthetic and no database is downloaded. The map appears once a key is saved.</span>
                    ) : !geoip.data.configured ? (
                      <span>No licence key saved. Target pages show no map.</span>
                    ) : geoip.data.available ? (
                      <span>
                        Database: <span className="font-mono">{geoip.data.edition}</span>{geoip.data.build_epoch ? ` built ${geoip.data.build_epoch.slice(0, 10)}` : ""}{geoip.data.downloaded_at ? ` · downloaded ${relTime(geoip.data.downloaded_at, now)}` : ""} · refreshed every {fmtDuration(geoip.data.refresh_after_sec)}.
                      </span>
                    ) : (
                      <span>Database not downloaded yet. It is fetched in the background shortly after the key is saved, or immediately with Download now.</span>
                    )
                  ) : (
                    <span>Checking the database…</span>
                  )}
                  {geoip.data?.last_error && <div className="mt-1 text-down">Last download failed: {geoip.data.last_error}</div>}
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
                {s.db_path && <><dt className="text-muted">DB path</dt><dd className="truncate text-right font-mono text-[11px]" title={s.db_path}>{s.db_path}</dd></>}
              </dl>
            ) : (
              <div className="text-sm text-faint">Loading…</div>
            )}
            {s?.simulate && <p className="mt-3 text-xs text-degraded">Simulation mode is on: paths are synthetic. Unset MTR_TRACKER_SIMULATE to send real probes.</p>}
            {s && !s.simulate && !s.mtr_version && <p className="mt-3 text-xs text-down">The mtr binary was not found. Install mtr / mtr-tiny or set MTR_TRACKER_MTR_BINARY.</p>}
          </section>
          <section className="card p-5">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><KeyRound size={15} /> API access</h2>
            <p className="text-xs text-muted">Everything in this UI is available as a JSON API, documented live at <a className="text-accent hover:underline" href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a>. Set <span className="font-mono">MTR_TRACKER_API_TOKEN</span> on the server to require a bearer token for all changes; reads stay open, but notification credentials and the webhook address are then shown masked unless the token is saved here.</p>
            <label className="label mt-3">Token for this browser</label>
            <div className="flex gap-2">
              <input className="input font-mono" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="only needed when the server sets a token" autoComplete="off" />
              <button className="btn" onClick={() => { setApiToken(token.trim()); toast(token.trim() ? "Token saved in this browser" : "Token cleared", "success"); }}>Save</button>
            </div>
            <div className="help">Stored in local storage only; never sent to anyone but this server.</div>
            <div className="mt-4 border-t border-border pt-3">
              <div className="mb-2 text-xs font-semibold">Targets backup</div>
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn btn-sm" onClick={exportTargets}><Download size={13} /> Export JSON</button>
                <select className="input w-auto py-1 text-xs" value={importMode} onChange={(e) => setImportMode(e.target.value as typeof importMode)} title="How to handle targets that already exist">
                  <option value="upsert">Import: update by name</option>
                  <option value="create">Import: always create</option>
                  <option value="replace">Import: replace all</option>
                </select>
                <button className="btn btn-sm" onClick={() => fileRef.current?.click()}><Upload size={13} /> Import JSON</button>
                <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
              </div>
              <div className="help">Same format as GET /api/targets/export. Useful for backups, cloning a server or managing targets from git.</div>
            </div>
          </section>
          <section className="card p-5 text-xs text-muted">
            <h2 className="mb-2 text-sm font-semibold text-text">Environment variables</h2>
            <ul className="space-y-1 font-mono">
              <li>MTR_TRACKER_PORT=8899</li>
              <li>MTR_TRACKER_DATA_DIR=/data</li>
              <li>MTR_TRACKER_MAX_CONCURRENT_RUNS=8</li>
              <li>MTR_TRACKER_MTR_BINARY=mtr</li>
              <li>MTR_TRACKER_SIMULATE=0</li>
              <li>MTR_TRACKER_API_TOKEN=</li>
            </ul>
            <p className="mt-2 font-sans">These are read at startup; restart the container to apply changes.</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
