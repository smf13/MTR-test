import type { Status, Target } from "./api";

export function fmtMs(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${v.toFixed(v < 10 ? Math.max(digits, 2) : digits)} ms`;
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return v.toFixed(digits);
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return `${v.toFixed(digits)}%`;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function fmtDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return s ? `${m}m ${s}s` : `${m}m`;
  }
  if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    const m = Math.round((sec % 3600) / 60);
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(sec / 86400);
  const h = Math.round((sec % 86400) / 3600);
  return h ? `${d}d ${h}h` : `${d}d`;
}

export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const diff = (now - new Date(iso).getTime()) / 1000;
  if (diff < 0) return `in ${fmtDuration(-diff)}`;
  if (diff < 5) return "just now";
  return `${fmtDuration(diff)} ago`;
}

export function fmtTime(iso: string | null | undefined, opts: { seconds?: boolean; date?: boolean } = {}): string {
  if (!iso) return "–";
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: opts.seconds ? "2-digit" : undefined });
  if (opts.date) return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
  return time;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function effectiveStatus(t: Pick<Target, "enabled" | "last_status">): Status {
  if (!t.enabled) return "paused";
  return t.last_status || "pending";
}

export const STATUS_LABEL: Record<Status, string> = {
  up: "Up",
  degraded: "Degraded",
  down: "Down",
  pending: "Pending",
  paused: "Paused",
};

export function statusColor(s: Status): string {
  switch (s) {
    case "up":
      return "var(--up)";
    case "degraded":
      return "var(--degraded)";
    case "down":
      return "var(--down)";
    default:
      return "var(--paused)";
  }
}

export function lossColor(loss: number | null | undefined, alpha = 1): string {
  if (loss === null || loss === undefined) return `rgba(148,163,184,${0.25 * alpha})`;
  if (loss <= 0) return `rgba(34,197,94,${0.75 * alpha})`;
  if (loss < 5) return `rgba(163,230,53,${0.85 * alpha})`;
  if (loss < 20) return `rgba(245,158,11,${0.9 * alpha})`;
  if (loss < 60) return `rgba(249,115,22,${0.95 * alpha})`;
  return `rgba(239,68,68,${alpha})`;
}

export function latencyColor(ms: number | null | undefined, max: number): string {
  if (ms === null || ms === undefined) return "rgba(148,163,184,0.25)";
  const r = Math.min(1, Math.max(0, max > 0 ? ms / max : 0));
  // blue (fast) -> amber -> red (slow)
  const hue = 200 - r * 200;
  return `hsl(${hue} 80% ${52 - r * 8}%)`;
}

export const RANGES: { value: string; label: string }[] = [
  { value: "1h", label: "1h" },
  { value: "6h", label: "6h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];

export function hopLabel(hop: { ip: string | null; hostname: string | null }): string {
  if (!hop.ip) return "No response";
  return hop.hostname || hop.ip;
}

export function classNames(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(" ");
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
