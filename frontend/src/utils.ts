import type { CSSProperties } from "react";
import type { Status, Target } from "./api";

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return v.toFixed(digits);
}

/** Decimals for a value in seconds: 1.23 s, 48.6 s, 7458465 s. */
const secDigits = (s: number) => (Math.abs(s) < 10 ? 2 : Math.abs(s) < 100 ? 1 : 0);

/**
 * A latency or jitter in milliseconds, split into number and unit: milliseconds below 1000 ms (judged after rounding,
 * so 999.97 never reads "1000.0 ms"), seconds from there on.
 */
export function msParts(v: number | null | undefined, digits = 1): { value: string; unit: "ms" | "s" | "" } {
  if (v === null || v === undefined || Number.isNaN(v)) return { value: "–", unit: "" };
  if (Math.abs(Number(v.toFixed(digits))) < 1000) return { value: v.toFixed(digits), unit: "ms" };
  const s = v / 1000;
  return { value: s.toFixed(secDigits(s)), unit: "s" };
}

/** A latency or jitter with its unit: "12.3 ms", or "1.23 s" from 1000 ms up; "–" when missing. */
export function fmtMs(v: number | null | undefined, digits = 1): string {
  const p = msParts(v, digits);
  return p.unit ? `${p.value} ${p.unit}` : p.value;
}

/** A cell of a column headed "(ms)": the bare number below 1000 ms, the value with "s" from there on. */
export function fmtMsCell(v: number | null | undefined, digits = 1): string {
  const p = msParts(v, digits);
  return p.unit === "s" ? `${p.value} s` : p.value;
}

/**
 * Latencies printed side by side ("10.0 / 15.0 ms", "best 10.0 · worst 15.0"): while all are below 1000 ms they stay
 * bare numbers sharing `unit` (" ms"); once one reaches seconds each text carries its own unit and `unit` is "".
 */
export function msGroup(values: (number | null | undefined)[], digits = 1): { texts: string[]; unit: string } {
  if (values.some((v) => msParts(v, digits).unit === "s")) return { texts: values.map((v) => fmtMs(v, digits)), unit: "" };
  return { texts: values.map((v) => fmtNum(v, digits)), unit: " ms" };
}

/**
 * `msGroup` joined into one text: `fmtMsJoin([10, 15])` = "10.0 / 15.0 ms", with `labels` "best 10.0 · worst 15.0 ms";
 * `trailingUnit: false` leaves the shared " ms" off (a tile whose value already names the unit).
 */
export function fmtMsJoin(values: (number | null | undefined)[], { sep = " / ", labels, digits = 1, trailingUnit = true }: { sep?: string; labels?: string[]; digits?: number; trailingUnit?: boolean } = {}): string {
  const { texts, unit } = msGroup(values, digits);
  return texts.map((t, i) => (labels ? `${labels[i]} ${t}` : t)).join(sep) + (trailingUnit ? unit : "");
}

/** An axis tick in milliseconds: "250 ms", "2.5 s". */
export function fmtMsTick(v: number, digits?: number): string {
  if (Math.abs(v) < 1000) return `${digits === undefined ? v : fmtNum(v, digits)} ms`;
  return `${Number((v / 1000).toFixed(3))} s`;
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
  // Round to the smallest unit shown first, so 59m 59.6s reads "1h", never "59m 60s" (nor "6d 24h").
  if (sec < 59.5) return `${Math.round(sec)}s`;
  if (sec < 3570) {
    const total = Math.round(sec);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s ? `${m}m ${s}s` : `${m}m`;
  }
  if (sec < 84600) {
    const total = Math.round(sec / 60);
    const h = Math.floor(total / 60);
    const m = total % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  const total = Math.round(sec / 3600);
  const d = Math.floor(total / 24);
  const h = total % 24;
  return h ? `${d}d ${h}h` : `${d}d`;
}

export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const diff = (now - new Date(iso).getTime()) / 1000;
  if (diff < 0) return `in ${fmtDuration(-diff)}`;
  if (diff < 5) return "just now";
  return `${fmtDuration(diff)} ago`;
}

// toLocale*String builds a new Intl formatter on every call, which cost seconds when charts formatted thousands of labels.
const TIME_FMT = new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit" });
const TIME_SEC_FMT = new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const DAY_FMT = new Intl.DateTimeFormat([], { month: "short", day: "numeric" });

export function fmtTime(iso: string | number | null | undefined, opts: { seconds?: boolean; date?: boolean } = {}): string {
  if (iso === null || iso === undefined || iso === "") return "–";
  const d = new Date(iso);
  const time = (opts.seconds ? TIME_SEC_FMT : TIME_FMT).format(d);
  if (opts.date) return `${DAY_FMT.format(d)} ${time}`;
  return time;
}

/** A time-axis label: the date alone at local midnight on a multi-day axis, otherwise the time of day. */
export function fmtAxisTime(v: number, multiDay: boolean): string {
  const d = new Date(v);
  if (multiDay && d.getHours() === 0 && d.getMinutes() === 0) return DAY_FMT.format(d);
  return TIME_FMT.format(d);
}

const TICK_STEPS_MIN = [1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 4320, 10080, 20160];

/**
 * A few round, local-time tick positions (epoch ms) across `domain` for a time axis `plotWidth` pixels wide.
 * Charts pass these as explicit `ticks`: without them Recharts treats every data point as a candidate tick and
 * formats and measures thousands of labels on each render, which froze the target page for seconds on a phone.
 */
export function timeTicks(domain: [number, number], plotWidth: number): number[] {
  const [start, end] = domain;
  const span = end - start;
  if (!(span > 0)) return [start];
  // n labels need n - 1 gaps of about 80 px ("12:00 PM" at 12 px is about 56 px, plus air between labels).
  const maxTicks = Math.max(2, Math.floor(plotWidth / 80) + 1);
  const stepMin = TICK_STEPS_MIN.find((s) => span / (s * 60_000) <= maxTicks) ?? Math.ceil(span / 60_000 / maxTicks / 10080) * 10080;
  const step = stepMin * 60_000;
  // Align to the browser's local clock, so ticks land on 14:00 or midnight rather than on UTC boundaries.
  const offset = new Date(start).getTimezoneOffset() * 60_000;
  const ticks: number[] = [];
  for (let t = Math.ceil((start - offset) / step) * step + offset; t <= end; t += step) ticks.push(t);
  return ticks;
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
  return `color-mix(in srgb, var(--heat-low), var(--heat-high) ${r * 100}%)`;
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

/** Slots of the categorical palette (`--series-1` … `--series-8` in index.css, stepped per theme). */
export const SERIES_SLOTS = 8;
/**
 * Line patterns that extend the palette past eight series: series 9-16 repeat the colours dashed, 17-24 dotted
 * and so on, so no two lines on a chart look alike up to SERIES_SLOTS × SERIES_DASHES.length series. More hues
 * would not help: past eight, generated colours stop being tellable apart.
 */
export const SERIES_DASHES = ["", "6 4", "1.5 3", "10 3 2 3", "14 5", "4 2 1 2"];

/** Colour of the i-th series (position on the chart, not a database id, so two shown series never share one). */
export function seriesColor(i: number): string {
  return `var(--series-${(((i % SERIES_SLOTS) + SERIES_SLOTS) % SERIES_SLOTS) + 1})`;
}

/** SVG stroke-dasharray of the i-th series; undefined (solid) for the first eight. */
export function seriesDash(i: number): string | undefined {
  return SERIES_DASHES[Math.floor(Math.max(0, i) / SERIES_SLOTS) % SERIES_DASHES.length] || undefined;
}

export function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const k = (s.length - 1) * p;
  const lo = Math.floor(k);
  const hi = Math.ceil(k);
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (k - lo);
}

const GLOBALPING_PATH = new Set(["mtr", "traceroute"]);
const GLOBALPING_PACKET = new Set(["ping", "mtr", "traceroute"]);

/** Types whose runs carry a hop list: the local mtr, or a Globalping mtr / traceroute measurement. */
export function isPathProbe(type: string | undefined, options?: { measurement?: string } | null): boolean {
  return !type || type === "mtr" || (type === "globalping" && GLOBALPING_PATH.has(options?.measurement ?? "ping"));
}

/** Types that send several packets and report packet loss, as opposed to one request (check) per run. */
export function isPacketProbe(type: string | undefined, options?: { measurement?: string } | null): boolean {
  return isPathProbe(type, options) || type === "ping" || (type === "globalping" && GLOBALPING_PACKET.has(options?.measurement ?? "ping"));
}

/** What the latency figure of a target means: response, connect or lookup time, or round-trip latency. */
export function latencyLabel(type: string | undefined, options?: { measurement?: string } | null): "Response" | "Connect" | "Lookup" | "Latency" {
  const kind = type === "globalping" ? (options?.measurement ?? "ping") : type;
  return kind === "http" ? "Response" : kind === "tcp" ? "Connect" : kind === "dns" ? "Lookup" : "Latency";
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

/** A new 256-bit Pushover end-to-end encryption key as 64 hex characters, from the browser's CSPRNG. */
export function randomKeyHex(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** off (empty), on (a 64-hex key, or a stored key the server masked) or invalid. */
export function pushoverKeyState(value: string): "off" | "on" | "invalid" {
  const v = value.replace(/\s+/g, "");
  if (!v) return "off";
  if (v.includes("*") || /^[0-9a-fA-F]{64}$/.test(v)) return "on";
  return "invalid";
}
