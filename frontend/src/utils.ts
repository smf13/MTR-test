import type { CSSProperties } from "react";
import type { Status, Target } from "./api";

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

/** Categorical palette for multi-target charts; readable on dark, OLED and light backgrounds. */
export const SERIES_COLORS = ["#38bdf8", "#f472b6", "#a3e635", "#fbbf24", "#a78bfa", "#fb923c", "#2dd4bf", "#f87171", "#60a5fa", "#e879f9", "#4ade80", "#facc15"];

export function seriesColor(i: number): string {
  return SERIES_COLORS[i % SERIES_COLORS.length];
}

export function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const k = (s.length - 1) * p;
  const lo = Math.floor(k);
  const hi = Math.ceil(k);
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (k - lo);
}

/** Types whose runs carry a hop list: the local mtr, or a Globalping mtr measurement. */
export function isPathProbe(type: string | undefined, options?: { measurement?: string } | null): boolean {
  return !type || type === "mtr" || (type === "globalping" && options?.measurement === "mtr");
}

/** Types that send several packets and report packet loss, as opposed to one request per run. */
export function isPacketProbe(type: string | undefined, options?: { measurement?: string } | null): boolean {
  return isPathProbe(type, options) || type === "ping" || type === "globalping";
}

/** Short label for the host of a target: strips the scheme for http probes so cards stay compact. */
export function hostLabel(host: string): string {
  return host.replace(/^https?:\/\//i, "");
}

/** Preset tag colours. One hex per preset works on light, dark and OLED because chips mix it with the theme colours. */
export const TAG_PRESETS: { name: string; hex: string }[] = [
  { name: "Slate", hex: "#64748b" },
  { name: "Red", hex: "#ef4444" },
  { name: "Orange", hex: "#f97316" },
  { name: "Amber", hex: "#f59e0b" },
  { name: "Lime", hex: "#84cc16" },
  { name: "Green", hex: "#22c55e" },
  { name: "Teal", hex: "#14b8a6" },
  { name: "Sky", hex: "#0ea5e9" },
  { name: "Blue", hex: "#3b82f6" },
  { name: "Violet", hex: "#8b5cf6" },
  { name: "Pink", hex: "#ec4899" },
  { name: "Rose", hex: "#f43f5e" },
];

/** Same order as the backend (`models.sort_tags`): case-insensitive, exact string as the tie-break. */
export function sortTags(tags: string[]): string[] {
  return [...tags].sort((a, b) => {
    const ka = a.toLowerCase();
    const kb = b.toLowerCase();
    if (ka !== kb) return ka < kb ? -1 : 1;
    return a < b ? -1 : a > b ? 1 : 0;
  });
}

/** Deterministic preset for tags without a configured colour, so a tag looks the same on every page. */
export function autoTagColor(tag: string): string {
  let h = 0;
  for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) | 0;
  return TAG_PRESETS[Math.abs(h) % TAG_PRESETS.length].hex;
}

/** Soft tinted chip that stays readable on every theme: the colour is mixed with the page text and made translucent. */
export function tagChipStyle(hex: string): CSSProperties {
  return {
    background: `color-mix(in srgb, ${hex} 16%, transparent)`,
    borderColor: `color-mix(in srgb, ${hex} 45%, transparent)`,
    color: `color-mix(in srgb, ${hex} 70%, var(--text))`,
  };
}
