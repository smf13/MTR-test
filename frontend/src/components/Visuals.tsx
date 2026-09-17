import { useMemo, useState } from "react";
import { Area, Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import type { Hop, HopSummary, HourlyBucket, OverviewSeries, Routes, SeriesPoint } from "../api";
import { HeatLegend } from "./HopHeatmap";
import { Sized } from "./Charts";
import { fmtNum, fmtTime, fmtDateTime, latencyColor, lossColor, percentile, seriesColor, classNames } from "../utils";

/* The status strip lives in StatusStrip.tsx so pages can show it without loading Recharts. */

/* ------------------------------------------------------------------ */
/* Multi-target latency overview                                        */
/* ------------------------------------------------------------------ */

export function OverviewChart({ data, height = 240 }: { data: OverviewSeries; height?: number }) {
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const targets = data.targets;
  const rows = useMemo(() => {
    const byT = new Map<number, Record<string, number | null>>();
    targets.forEach((t) => {
      t.points.forEach((p) => {
        const ts = new Date(p.t).getTime();
        const row = byT.get(ts) ?? { t: ts };
        row[`v${t.id}`] = p.avg;
        byT.set(ts, row);
      });
    });
    return [...byT.values()].sort((a, b) => (a.t as number) - (b.t as number));
  }, [targets]);
  const domain = useMemo<[number, number]>(() => {
    const now = Date.now();
    const start = now - data.range_sec * 1000;
    const first = rows.length ? (rows[0].t as number) : start;
    return [Math.max(start, Math.min(first, now - 60_000)), now];
  }, [rows, data.range_sec]);
  if (!targets.length) return <div className="flex items-center justify-center text-sm text-faint" style={{ height }}>No runs yet.</div>;
  const tick = (v: number) => fmtTime(new Date(v).toISOString(), { date: data.range_sec > 86400 });
  return (
    <div>
      <Sized height={height}>
        {(width) => (
          <ComposedChart width={width} height={height} data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="t" type="number" domain={domain} scale="time" tickFormatter={tick} tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} minTickGap={48} />
            <YAxis tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={64} tickFormatter={(v: number) => `${v} ms`} domain={[0, "auto"]} />
            <Tooltip
              isAnimationActive={false}
              cursor={{ stroke: "var(--border-strong)" }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const items = payload.filter((p) => p.value !== null && p.value !== undefined).sort((a, b) => Number(b.value) - Number(a.value));
                return (
                  <div className="chart-tooltip">
                    <div className="mb-1 font-semibold">{fmtTime(new Date(Number(label)).toISOString(), { date: data.range_sec > 86400 })}</div>
                    <div className="num grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                      {items.map((p) => (
                        <span key={String(p.dataKey)} className="contents">
                          <span className="flex items-center gap-1.5 text-muted"><span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color }} />{p.name}</span>
                          <span className="text-right">{fmtNum(Number(p.value))} ms</span>
                        </span>
                      ))}
                    </div>
                  </div>
                );
              }}
            />
            {targets.map((t) => (
              <Line key={t.id} name={t.name} dataKey={`v${t.id}`} type="monotone" stroke={seriesColor(t.id)} strokeWidth={1.6} dot={false} isAnimationActive={false} connectNulls={false} hide={hidden.has(t.id)} activeDot={{ r: 3 }} />
            ))}
          </ComposedChart>
        )}
      </Sized>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 px-1">
        {targets.map((t) => (
          <button
            key={t.id}
            className={classNames("inline-flex items-center gap-1.5 rounded px-1 text-xs transition-opacity", hidden.has(t.id) ? "opacity-40" : "opacity-100")}
            onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(t.id)) n.delete(t.id); else n.add(t.id); return n; })}
            title={hidden.has(t.id) ? "Show" : "Hide"}
          >
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: seriesColor(t.id) }} />
            <span className={classNames(hidden.has(t.id) && "line-through")}>{t.name}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Path profile: latency and loss per hop                               */
/* ------------------------------------------------------------------ */

interface ProfileRow {
  hop: number;
  label: string;
  avg: number | null;
  band: [number, number] | null;
  loss: number;
  noReply: boolean;
}

export function profileFromHops(hops: Hop[]): ProfileRow[] {
  return hops.map((h) => ({
    hop: h.hop_no,
    label: h.ip ? h.hostname || h.ip : "no response",
    avg: h.avg_ms,
    band: h.best_ms !== null && h.worst_ms !== null ? [h.best_ms, h.worst_ms] : null,
    loss: h.loss_pct,
    noReply: !h.ip,
  }));
}

export function profileFromSummary(summary: HopSummary): ProfileRow[] {
  return summary.hops.map((h) => ({
    hop: h.hop,
    label: h.primary.ip ? h.primary.hostname || h.primary.ip : "no response",
    avg: h.primary.avg_ms,
    band: h.primary.best_ms !== null && h.primary.worst_ms !== null ? [h.primary.best_ms, h.primary.worst_ms] : null,
    loss: h.primary.loss_pct,
    noReply: !h.primary.ip,
  }));
}

export function PathProfileChart({ rows, height = 240 }: { rows: ProfileRow[]; height?: number }) {
  if (!rows.length) return <div className="flex items-center justify-center text-sm text-faint" style={{ height }}>No hop data.</div>;
  return (
    <Sized height={height}>
      {(width) => (
        <ComposedChart width={width} height={height} data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="profBand" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-band)" stopOpacity={0.3} />
              <stop offset="100%" stopColor="var(--chart-band)" stopOpacity={0.06} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="hop" tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} />
          <YAxis yAxisId="ms" tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={64} tickFormatter={(v: number) => `${v} ms`} domain={[0, "auto"]} />
          <YAxis yAxisId="loss" orientation="right" domain={[0, 100]} ticks={[0, 50, 100]} tickFormatter={(v: number) => `${v}%`} tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={40} />
          <Tooltip
            isAnimationActive={false}
            cursor={{ fill: "var(--surface-2)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const r = payload[0].payload as ProfileRow;
              return (
                <div className="chart-tooltip">
                  <div className="font-semibold">Hop {r.hop} · <span className="font-mono font-normal">{r.label}</span></div>
                  <div className="num mt-1 grid grid-cols-[auto_1fr] gap-x-3">
                    <span className="text-muted">Avg</span><span className="text-right">{r.avg !== null ? `${fmtNum(r.avg)} ms` : "–"}</span>
                    {r.band && <><span className="text-muted">Best / Worst</span><span className="text-right">{fmtNum(r.band[0])} / {fmtNum(r.band[1])} ms</span></>}
                    <span className="text-muted">Loss</span><span className="text-right" style={{ color: r.loss > 0 ? lossColor(r.loss) : undefined }}>{fmtNum(r.loss)}%</span>
                  </div>
                </div>
              );
            }}
          />
          <Bar yAxisId="loss" dataKey="loss" barSize={10} isAnimationActive={false} radius={[2, 2, 0, 0]}>
            {rows.map((r) => (
              <Cell key={r.hop} fill={r.loss > 0 ? lossColor(r.loss, 0.85) : "rgba(148,163,184,0.12)"} />
            ))}
          </Bar>
          <Area yAxisId="ms" type="monotone" dataKey="band" stroke="none" fill="url(#profBand)" isAnimationActive={false} connectNulls dot={false} activeDot={false} />
          <Line yAxisId="ms" type="monotone" dataKey="avg" stroke="var(--chart-avg)" strokeWidth={2} isAnimationActive={false} connectNulls dot={{ r: 3, fill: "var(--chart-avg)", strokeWidth: 0 }} activeDot={{ r: 4 }} />
        </ComposedChart>
      )}
    </Sized>
  );
}

/* ------------------------------------------------------------------ */
/* Latency distribution histogram                                       */
/* ------------------------------------------------------------------ */

/** One dashed percentile marker on the histogram; markers with the same value are merged into a single label. */
interface PercentileMarker {
  keys: string[];
  value: number;
  color: string;
}

/** Y axis width + margins of the histogram; needed to estimate where the markers land in pixels. */
const HIST_Y_AXIS_W = 44;
const HIST_MARGIN_RIGHT = 16;
const HIST_LABEL_ROW_H = 15;
const HIST_LABEL_FONT = 12;

function percentileMarkers(p50: number | null, p95: number | null, p99: number | null): PercentileMarker[] {
  const out: PercentileMarker[] = [];
  const add = (key: string, value: number | null, color: string) => {
    if (value === null) return;
    const same = out.find((m) => Math.abs(m.value - value) < 1e-9);
    if (same) same.keys.push(key);
    else out.push({ keys: [key], value, color });
  };
  add("p50", p50, "var(--up)");
  add("p95", p95, "var(--degraded)");
  add("p99", p99, "var(--down)");
  return out;
}

/**
 * Assign each marker label a row (0 = closest to the chart) so labels whose x positions are
 * within a label's width of each other are stacked instead of drawn on top of one another.
 * Only the relative pixel positions matter, so an estimate of the plot scale is good enough.
 */
export function layoutPercentileLabels(markers: PercentileMarker[], edges: [number, number], plotWidth: number): number[] {
  const [min, max] = edges;
  const span = max - min || 1;
  const px = (v: number) => ((v - min) / span) * Math.max(1, plotWidth);
  const labelW = (m: PercentileMarker) => m.keys.join("/").length * (HIST_LABEL_FONT * 0.62) + 6;
  const order = markers.map((_, i) => i).sort((a, b) => markers[a].value - markers[b].value);
  const rows = new Array<number>(markers.length).fill(0);
  const lastInRow: { x: number; w: number }[] = [];
  order.forEach((i) => {
    const x = px(markers[i].value);
    const w = labelW(markers[i]);
    let row = 0;
    while (row < lastInRow.length && Math.abs(x - lastInRow[row].x) < (w + lastInRow[row].w) / 2 + 2) row += 1;
    rows[i] = row;
    lastInRow[row] = { x, w };
  });
  return rows;
}

export function LatencyHistogram({ points, height = 220, bins = 30 }: { points: SeriesPoint[]; height?: number; bins?: number }) {
  const { rows, p50, p95, p99, count, binWidth, edges } = useMemo(() => {
    const vals = points.filter((p) => p.ok && p.avg !== null).map((p) => p.avg as number);
    if (vals.length < 2) return { rows: [] as { x: number; label: string; n: number }[], p50: null, p95: null, p99: null, count: vals.length, binWidth: 0, edges: [0, 1] as [number, number] };
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1;
    const nb = Math.min(bins, Math.max(8, Math.ceil(Math.sqrt(vals.length))));
    const width = span / nb;
    const counts = new Array(nb).fill(0);
    vals.forEach((v) => {
      const i = Math.min(nb - 1, Math.floor((v - min) / width));
      counts[i] += 1;
    });
    return {
      rows: counts.map((n, i) => ({ x: min + i * width + width / 2, label: `${fmtNum(min + i * width, 1)}–${fmtNum(min + (i + 1) * width, 1)} ms`, n })),
      p50: percentile(vals, 0.5),
      p95: percentile(vals, 0.95),
      p99: percentile(vals, 0.99),
      count: vals.length,
      binWidth: width,
      edges: [min, max] as [number, number],
    };
  }, [points, bins]);
  const markers = useMemo(() => percentileMarkers(p50, p95, p99), [p50, p95, p99]);
  if (!rows.length) return <div className="flex items-center justify-center text-sm text-faint" style={{ height }}>Need at least two reachable runs.</div>;
  return (
    <div>
      <Sized height={height}>
        {(width) => {
          const labelRows = layoutPercentileLabels(markers, edges, width - HIST_Y_AXIS_W - HIST_MARGIN_RIGHT);
          const rowCount = labelRows.length ? Math.max(...labelRows) + 1 : 1;
          return (
          <ComposedChart width={width} height={height} data={rows} margin={{ top: 6 + rowCount * HIST_LABEL_ROW_H, right: HIST_MARGIN_RIGHT, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="x" type="number" domain={edges} tickFormatter={(v: number) => fmtNum(v, 0)} tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} unit=" ms" />
            <YAxis tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={HIST_Y_AXIS_W} allowDecimals={false} />
            <Tooltip
              isAnimationActive={false}
              cursor={{ fill: "var(--surface-2)" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const r = payload[0].payload as { label: string; n: number };
                return (
                  <div className="chart-tooltip">
                    <div className="font-semibold">{r.label}</div>
                    <div className="num text-muted">{r.n} run{r.n === 1 ? "" : "s"} · {((100 * r.n) / count).toFixed(1)}%</div>
                  </div>
                );
              }}
            />
            <Bar dataKey="n" fill="var(--chart-avg)" fillOpacity={0.75} isAnimationActive={false} barSize={Math.max(3, Math.floor((width - 70) / rows.length) - 2)} radius={[2, 2, 0, 0]} />
            {markers.map((m, i) => (
              <ReferenceLine
                key={m.keys.join("/")}
                x={m.value}
                stroke={m.color}
                strokeDasharray="4 3"
                label={(props: { viewBox?: { x?: number; y?: number; width?: number } }) => {
                  // viewBox is the line's bounding box (width 0 for a vertical line); stack colliding labels upwards by row.
                  const vb = props.viewBox ?? {};
                  const lx = Math.min(Math.max((vb.x ?? 0) + (vb.width ?? 0) / 2, HIST_Y_AXIS_W + 14), width - HIST_MARGIN_RIGHT - 14);
                  const ly = (vb.y ?? 0) - 4 - labelRows[i] * HIST_LABEL_ROW_H;
                  return (
                    <text x={lx} y={ly} fontSize={HIST_LABEL_FONT} fill={m.color} textAnchor="middle" fontFamily="var(--font-mono)">
                      {m.keys.join("/")}
                    </text>
                  );
                }}
              />
            ))}
          </ComposedChart>
          );
        }}
      </Sized>
      <div className="num mt-1 flex flex-wrap gap-x-4 px-1 text-xs text-faint">
        <span>{count} runs</span>
        <span>bin {fmtNum(binWidth, 2)} ms</span>
        <span style={{ color: "var(--up)" }}>p50 {fmtNum(p50)} ms</span>
        <span style={{ color: "var(--degraded)" }}>p95 {fmtNum(p95)} ms</span>
        <span style={{ color: "var(--down)" }}>p99 {fmtNum(p99)} ms</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Day × hour heatmap                                                   */
/* ------------------------------------------------------------------ */

export type HourlyMetric = "avg" | "loss" | "jitter";

export function HourlyHeatmap({ hours, metric }: { hours: HourlyBucket[]; metric: HourlyMetric }) {
  const [hover, setHover] = useState<{ x: number; y: number; b: HourlyBucket } | null>(null);
  const { days, grid, max } = useMemo(() => {
    const grid = new Map<string, (HourlyBucket | null)[]>();
    let max = 0;
    hours.forEach((b) => {
      const d = new Date(b.t);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const row = grid.get(key) ?? new Array(24).fill(null);
      row[d.getHours()] = b;
      grid.set(key, row);
      const v = metric === "avg" ? b.avg : metric === "jitter" ? b.jitter : b.loss;
      if (v !== null && v > max) max = v;
    });
    return { days: [...grid.keys()].sort(), grid, max: max || 1 };
  }, [hours, metric]);
  if (!days.length) return <div className="flex h-32 items-center justify-center text-sm text-faint">No runs in this range.</div>;
  const cellW = 34;
  const cellH = 20;
  const labelW = 104;
  const width = labelW + 24 * (cellW + 1);
  const height = days.length * (cellH + 1) + 18;
  const valueOf = (b: HourlyBucket) => (metric === "avg" ? b.avg : metric === "jitter" ? b.jitter : b.loss);
  return (
    <div>
      <HeatLegend metric={metric} max={max} />
      <div className="relative overflow-x-auto">
      <svg width={width} height={height} className="block select-none" onMouseLeave={() => setHover(null)}>
        {Array.from({ length: 24 }, (_, h) => (
          <text key={h} x={labelW + h * (cellW + 1) + cellW / 2} y={11} fontSize={12} textAnchor="middle" fill="var(--text-faint)">
            {h % 3 === 0 ? `${String(h).padStart(2, "0")}` : ""}
          </text>
        ))}
        {days.map((day, ri) => {
          const y = 16 + ri * (cellH + 1);
          const d = new Date(`${day}T00:00:00`);
          return (
            <g key={day}>
              <text x={labelW - 8} y={y + cellH - 4} fontSize={12} textAnchor="end" fill="var(--text-muted)">
                {d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}
              </text>
              {grid.get(day)!.map((b, h) => {
                const v = b ? valueOf(b) : null;
                let fill = "var(--surface-2)";
                if (b) {
                  if (b.ok_n === 0 && b.n > 0) fill = "rgba(239,68,68,0.9)";
                  else if (metric === "loss") fill = lossColor(v ?? 0);
                  else fill = latencyColor(v, max);
                }
                return (
                  <rect key={h} x={labelW + h * (cellW + 1)} y={y} width={cellW} height={cellH} rx={2} fill={fill} opacity={b ? 0.92 : 1} stroke={hover?.b === b && b ? "var(--text)" : "none"} onMouseEnter={() => b && setHover({ x: labelW + h * (cellW + 1) + cellW, y, b })} />
                );
              })}
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="card pointer-events-none absolute z-10 px-2.5 py-1.5 text-xs" style={{ left: Math.min(hover.x + 8, width - 220), top: hover.y + 20, borderColor: "var(--border-strong)" }}>
          <div className="font-semibold">{fmtDateTime(hover.b.t).replace(/:\d\d:\d\d/, ":00")}</div>
          <div className="num mt-0.5 grid grid-cols-[auto_1fr] gap-x-3">
            <span className="text-muted">Avg</span><span>{hover.b.avg !== null ? `${fmtNum(hover.b.avg)} ms` : "unreachable"}</span>
            <span className="text-muted">Worst</span><span>{fmtNum(hover.b.worst)} ms</span>
            <span className="text-muted">Loss</span><span>{fmtNum(hover.b.loss)}% (max {fmtNum(hover.b.max_loss)}%)</span>
            <span className="text-muted">Jitter</span><span>{fmtNum(hover.b.jitter)} ms</span>
            <span className="text-muted">Runs</span><span>{hover.b.ok_n}/{hover.b.n} reached</span>
          </div>
        </div>
      )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Route timeline                                                       */
/* ------------------------------------------------------------------ */

const ROUTE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export function routeLabel(i: number): string {
  return i < ROUTE_LETTERS.length ? ROUTE_LETTERS[i] : `R${i + 1}`;
}

export function RouteTimeline({ routes, onOpenRun }: { routes: Routes; onOpenRun?: (runId: number) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!routes.segments.length) return <div className="py-4 text-center text-xs text-faint">No completed runs in this range.</div>;
  const end = Date.now();
  const rangeStart = new Date(routes.since).getTime();
  const firstSeen = new Date(routes.segments[0].start).getTime();
  // Same rule as the RTT chart: full range when data covers it, otherwise from the first run.
  const start = Math.max(rangeStart, Math.min(firstSeen, end - 60_000));
  const span = end - start || 1;
  return (
    <div>
      <div className="relative flex h-4 w-full overflow-hidden rounded" style={{ background: "var(--surface-2)" }}>
        {routes.segments.map((seg, i) => {
          const s = new Date(seg.start).getTime();
          const e = new Date(seg.end).getTime();
          const left = Math.max(0, ((s - start) / span) * 100);
          const width = Math.max(0.15, ((e - s) / span) * 100);
          return (
            <div
              key={i}
              className="absolute top-0 h-full cursor-pointer"
              style={{ left: `${left}%`, width: `${width}%`, background: seriesColor(seg.index), opacity: hover === null || hover === seg.index ? 0.95 : 0.35 }}
              title={`Route ${routeLabel(seg.index)} · ${seg.hops} hops · ${seg.runs} run${seg.runs === 1 ? "" : "s"} · ${fmtTime(seg.start, { date: routes.range_sec > 86400 })} – ${fmtTime(seg.end, { date: routes.range_sec > 86400 })}`}
              onMouseEnter={() => setHover(seg.index)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onOpenRun?.(seg.first_run_id)}
            />
          );
        })}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {routes.routes.slice(0, 12).map((r) => (
          <button key={r.hash} className={classNames("inline-flex items-center gap-1.5 rounded px-1 transition-opacity", hover !== null && hover !== r.index && "opacity-40")} onMouseEnter={() => setHover(r.index)} onMouseLeave={() => setHover(null)} onClick={() => onOpenRun?.(r.example_run_id)} title={`First seen ${fmtDateTime(r.first_seen)} · last seen ${fmtDateTime(r.last_seen)} · reached ${r.reached}/${r.runs}`}>
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: seriesColor(r.index) }} />
            <span className="font-semibold">Route {routeLabel(r.index)}</span>
            <span className="num text-muted">{r.hops} hops · {r.share_pct}% · {r.runs} runs</span>
          </button>
        ))}
        {routes.routes.length > 12 && <span className="text-faint">+{routes.routes.length - 12} more</span>}
      </div>
    </div>
  );
}
