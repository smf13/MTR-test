import { useEffect, useState } from "react";
import type { IpVersion, Protocol, Target, TargetInput } from "../api";
import { Modal } from "./Modal";
import { NumberInput } from "./NumberInput";
import { classNames } from "../utils";

const DEFAULTS: TargetInput = {
  name: "",
  host: "",
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
  alert_loss_pct: 5,
  alert_latency_ms: 200,
};

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
  onClose,
  onSubmit,
  submitting,
}: {
  open: boolean;
  initial?: Target | null;
  prefill?: Partial<TargetInput> | null;
  onClose: () => void;
  onSubmit: (values: TargetInput) => Promise<void>;
  submitting: boolean;
}) {
  const [form, setForm] = useState<TargetInput>(DEFAULTS);
  const [tagText, setTagText] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      const { name, host, description, tags, interval_sec, count, probe_interval, protocol, port, packet_size, ip_version, max_hops, enabled, alert_loss_pct, alert_latency_ms } = initial;
      setForm({ name, host, description, tags, interval_sec, count, probe_interval, protocol, port, packet_size, ip_version, max_hops, enabled, alert_loss_pct, alert_latency_ms });
      setTagText(tags.join(", "));
      setAdvanced(protocol !== "icmp" || packet_size !== 64 || max_hops !== 30 || ip_version !== "auto" || probe_interval !== 1);
    } else {
      setForm({ ...DEFAULTS, ...(prefill ?? {}) });
      setTagText("");
      setAdvanced(false);
    }
    setError(null);
  }, [open, initial, prefill]);

  const set = <K extends keyof TargetInput>(k: K, v: TargetInput[K]) => setForm((f) => ({ ...f, [k]: v }));
  const runDuration = Math.round(form.count * form.probe_interval);
  const durationWarn = runDuration > form.interval_sec * 0.8;

  const submit = async () => {
    setError(null);
    if (!form.name.trim()) return setError("Name is required.");
    if (!form.host.trim()) return setError("Host is required.");
    if (durationWarn) return setError(`A run takes about ${runDuration}s (probes × probe interval), which is too close to the ${form.interval_sec}s schedule. Increase the interval or lower the probe count.`);
    const tags = tagText.split(",").map((t) => t.trim()).filter(Boolean);
    try {
      await onSubmit({ ...form, name: form.name.trim(), host: form.host.trim(), tags, port: form.protocol === "icmp" ? null : form.port });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initial ? `Edit ${initial.name}` : "Add target"}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? "Saving…" : initial ? "Save changes" : "Add target"}
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
        <div>
          <label className="label">Name</label>
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Head office WAN" autoFocus />
        </div>
        <div>
          <label className="label">Host or IP</label>
          <input className="input font-mono" value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="1.1.1.1 or vpn.example.com" spellCheck={false} />
        </div>
        <div className="sm:col-span-2">
          <label className="label">Description (optional)</label>
          <input className="input" value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="What this target represents and why it matters" />
        </div>
        <div className="sm:col-span-2">
          <label className="label">Tags (comma separated)</label>
          <input className="input" value={tagText} onChange={(e) => setTagText(e.target.value)} placeholder="wan, isp-a, critical" />
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
          <div className="help">Seconds between MTR runs (10 – 86400).</div>
        </div>
        <div>
          <label className="label">Probes per hop</label>
          <NumberInput className="input num" min={1} max={200} value={form.count} onChange={(v) => set("count", v ?? form.count)} />
          <div className={classNames("help", durationWarn && "!text-degraded")}>
            Each run sends {form.count} probes per hop and takes about {runDuration}s.
          </div>
        </div>

        <div>
          <label className="label">Alert when packet loss ≥ (%)</label>
          <NumberInput className="input num" min={0} max={100} step={0.5} value={form.alert_loss_pct} onChange={(v) => set("alert_loss_pct", v ?? form.alert_loss_pct)} />
          <div className="help">0 disables the loss alert.</div>
        </div>
        <div>
          <label className="label">Alert when avg latency ≥ (ms)</label>
          <NumberInput className="input num" min={0} step={1} value={form.alert_latency_ms} onChange={(v) => set("alert_latency_ms", v ?? form.alert_latency_ms)} />
          <div className="help">0 disables the latency alert.</div>
        </div>

        <div className="sm:col-span-2">
          <button type="button" className="text-xs font-medium text-accent hover:underline" onClick={() => setAdvanced((a) => !a)}>
            {advanced ? "Hide" : "Show"} advanced probe options
          </button>
        </div>
        {advanced && (
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
            <div>
              <label className="label">IP version</label>
              <select className="input" value={form.ip_version} onChange={(e) => set("ip_version", e.target.value as IpVersion)}>
                <option value="auto">Auto (prefer IPv4)</option>
                <option value="4">IPv4 only</option>
                <option value="6">IPv6 only</option>
              </select>
            </div>
            <div>
              <label className="label">Probe interval (s)</label>
              <NumberInput className="input num" min={0.1} max={10} step={0.1} value={form.probe_interval} onChange={(v) => set("probe_interval", v ?? form.probe_interval)} />
              <div className="help">Delay between probes, mtr -i.</div>
            </div>
            <div>
              <label className="label">Packet size (bytes)</label>
              <NumberInput className="input num" min={28} max={1500} value={form.packet_size} onChange={(v) => set("packet_size", v ?? form.packet_size)} />
            </div>
            <div>
              <label className="label">Max hops</label>
              <NumberInput className="input num" min={1} max={64} value={form.max_hops} onChange={(v) => set("max_hops", v ?? form.max_hops)} />
            </div>
          </>
        )}

        <label className="sm:col-span-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Monitoring enabled
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
