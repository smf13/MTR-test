import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Area, Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import type { SeriesPoint } from "../api";
import { fmtNum, fmtTime, lossColor } from "../utils";

interface Row {
  t: number;
  avg: number | null;
  band: [number, number] | null;
  loss: number;
  maxLoss: number;
  jitter: number | null;
  hops: number | null;
  ok: boolean;
  routeChanged: boolean;
  n: number;
  runId: number | null;
}

function toRows(points: SeriesPoint[]): Row[] {
  return points.map((p) => ({
    t: new Date(p.t).getTime(),
    avg: p.ok || p.avg !== null ? p.avg : null,
    band: p.best !== null && p.worst !== null ? [p.best, p.worst] : null,
    loss: p.loss ?? (p.ok ? 0 : 100),
    maxLoss: p.max_loss ?? p.loss ?? (p.ok ? 0 : 100),
    jitter: p.jitter,
    hops: p.hops,
    ok: p.ok,
    routeChanged: p.route_changed,
    n: p.n,
    runId: p.run_id,
  }));
}

/** Time axis: full range when data covers it, otherwise from the first point so new targets are readable. */
function useDomain(rows: Row[], rangeSec: number): [number, number] {
  return useMemo<[number, number]>(() => {
    const now = Date.now();
    const start = now - rangeSec * 1000;
    const first = rows.length ? rows[0].t : start;
    const lo = Math.max(start, Math.min(first, now - 60_000));
    return [lo, now];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, rangeSec]);
}

/** Measures its own width with a ResizeObserver and renders the chart at explicit pixel size. */
export function Sized({ height, children }: { height: number; children: (width: number) => ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setWidth(Math.floor(el.getBoundingClientRect().width));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={ref} style={{ width: "100%", height }}>
      {width > 0 ? children(width) : null}
    </div>
  );
}

function tickFormatter(rangeSec: number) {
  return (v: number) => fmtTime(new Date(v).toISOString(), { date: rangeSec > 86400 });
}

function TooltipBox({ active, payload, rangeSec, bucketSec }: { active?: boolean; payload?: { payload: Row }[]; rangeSec: number; bucketSec: number | null }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div className="mb-1 font-semibold">
        {fmtTime(new Date(r.t).toISOString(), { seconds: !bucketSec, date: rangeSec > 86400 })}
        {bucketSec ? <span className="ml-1 font-normal text-faint">({r.n} runs)</span> : null}
      </div>
      <div className="num grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        <span className="text-muted">Avg</span>
        <span className="text-right font-semibold">{r.avg !== null ? `${fmtNum(r.avg)} ms` : "unreachable"}</span>
        {r.band && (
          <>
            <span className="text-muted">Best / Worst</span>
            <span className="text-right">
              {fmtNum(r.band[0])} / {fmtNum(r.band[1])} ms
            </span>
          </>
        )}
        <span className="text-muted">Loss</span>
        <span className="text-right" style={{ color: r.loss > 0 ? lossColor(r.loss) : undefined }}>
          {fmtNum(r.loss)}%{bucketSec && r.maxLoss !== r.loss ? ` (max ${fmtNum(r.maxLoss)}%)` : ""}
        </span>
        {r.jitter !== null && (
          <>
            <span className="text-muted">Jitter</span>
            <span className="text-right">{fmtNum(r.jitter)} ms</span>
          </>
        )}
        {r.hops !== null && (
          <>
            <span className="text-muted">Hops</span>
            <span className="text-right">{r.hops}</span>
          </>
        )}
        {r.routeChanged && <span className="col-span-2 mt-1 font-medium" style={{ color: "var(--chart-jitter)" }}>Route changed</span>}
      </div>
    </div>
  );
}

/** Y-axis width plus the right margin of the latency chart: what the time axis does not get. */
const LATENCY_PLOT_INSET = 64 + 12;

/**
 * Route-change markers to draw: every flagged run across the whole range, thinned so no two markers sit closer
 * than `minGapPx`. Density therefore stays uniform from the oldest run to the newest instead of the markers
 * being cut off after a fixed count, which used to hide everything older than the last few hours on a
 * load-balanced path that flags most runs.
 */
export function thinRouteChanges<T extends { t: number; routeChanged: boolean }>(rows: T[], domain: [number, number], plotWidth: number, minGapPx = 3): T[] {
  const span = Math.max(1, domain[1] - domain[0]);
  const pxPerMs = Math.max(1, plotWidth) / span;
  const out: T[] = [];
  let lastX = -Infinity;
  for (const r of rows) {
    if (!r.routeChanged) continue;
    const x = (r.t - domain[0]) * pxPerMs;
    if (x - lastX < minGapPx) continue;
    out.push(r);
    lastX = x;
  }
  return out;
}

export function LatencyChart({ points, rangeSec, bucketSec, height = 260, onPointClick }: { points: SeriesPoint[]; rangeSec: number; bucketSec: number | null; height?: number; onPointClick?: (runId: number) => void }) {
  const rows = useMemo(() => toRows(points), [points]);
  const domain = useDomain(rows, rangeSec);
  if (!rows.length) return <Empty height={height} />;
  return (
    <Sized height={height}>
      {(width) => (
      <ComposedChart width={width} height={height} data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} onClick={(e) => {
        const p = (e as { activePayload?: { payload: Row }[] })?.activePayload?.[0]?.payload;
        if (p?.runId && onPointClick) onPointClick(p.runId);
      }}>
        <defs>
          <linearGradient id="latBand" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-band)" stopOpacity={0.28} />
            <stop offset="100%" stopColor="var(--chart-band)" stopOpacity={0.08} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
        <XAxis dataKey="t" type="number" domain={domain} scale="time" tickFormatter={tickFormatter(rangeSec)} tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} minTickGap={48} />
        <YAxis tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={64} tickFormatter={(v: number) => `${v} ms`} domain={[0, "auto"]} />
        <Tooltip content={<TooltipBox rangeSec={rangeSec} bucketSec={bucketSec} />} cursor={{ stroke: "var(--border-strong)" }} isAnimationActive={false} />
        {thinRouteChanges(rows, domain, width - LATENCY_PLOT_INSET).map((r) => (
          <ReferenceLine key={r.t} x={r.t} stroke="var(--chart-jitter)" strokeDasharray="3 3" strokeOpacity={0.7} />
        ))}
        <Area type="monotone" dataKey="band" stroke="none" fill="url(#latBand)" isAnimationActive={false} connectNulls={false} dot={false} activeDot={false} />
        <Line type="monotone" dataKey="avg" stroke="var(--chart-avg)" strokeWidth={1.8} dot={false} isAnimationActive={false} connectNulls={false} activeDot={{ r: 3 }} />
      </ComposedChart>
      )}
    </Sized>
  );
}

export function LossChart({ points, rangeSec, bucketSec, height = 120 }: { points: SeriesPoint[]; rangeSec: number; bucketSec: number | null; height?: number }) {
  const rows = useMemo(() => toRows(points), [points]);
  const domain = useDomain(rows, rangeSec);
  if (!rows.length) return <Empty height={height} />;
  const barSize = Math.max(1.5, Math.min(10, 900 / rows.length));
  return (
    <Sized height={height}>
      {(width) => (
      <ComposedChart width={width} height={height} data={rows} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
        <XAxis dataKey="t" type="number" domain={domain} scale="time" tickFormatter={tickFormatter(rangeSec)} tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} minTickGap={48} />
        <YAxis tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={44} domain={[0, 100]} ticks={[0, 50, 100]} tickFormatter={(v: number) => `${v}%`} />
        <Tooltip content={<TooltipBox rangeSec={rangeSec} bucketSec={bucketSec} />} cursor={{ fill: "var(--surface-2)" }} isAnimationActive={false} />
        <Bar dataKey="maxLoss" barSize={barSize} isAnimationActive={false} minPointSize={1}>
          {rows.map((r) => (
            <Cell key={r.t} fill={r.maxLoss > 0 ? lossColor(r.maxLoss) : "rgba(148,163,184,0.18)"} />
          ))}
        </Bar>
      </ComposedChart>
      )}
    </Sized>
  );
}

export function JitterChart({ points, rangeSec, bucketSec, height = 120 }: { points: SeriesPoint[]; rangeSec: number; bucketSec: number | null; height?: number }) {
  const rows = useMemo(() => toRows(points), [points]);
  const domain = useDomain(rows, rangeSec);
  if (!rows.length) return <Empty height={height} />;
  return (
    <Sized height={height}>
      {(width) => (
      <ComposedChart width={width} height={height} data={rows} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
        <XAxis dataKey="t" type="number" domain={domain} scale="time" tickFormatter={tickFormatter(rangeSec)} tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} minTickGap={48} />
        <YAxis tick={{ fontSize: 12, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={64} tickFormatter={(v: number) => `${v} ms`} domain={[0, "auto"]} />
        <Tooltip content={<TooltipBox rangeSec={rangeSec} bucketSec={bucketSec} />} cursor={{ stroke: "var(--border-strong)" }} isAnimationActive={false} />
        <Line type="monotone" dataKey="jitter" stroke="var(--chart-jitter)" strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
      </ComposedChart>
      )}
    </Sized>
  );
}

function Empty({ height }: { height: number }) {
  return (
    <div className="flex items-center justify-center text-sm text-faint" style={{ height }}>
      No runs in this range yet.
    </div>
  );
}
