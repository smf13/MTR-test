import { useMemo, useState } from "react";
import type { HopHistory } from "../api";
import { fmtNum, fmtTime, latencyColor, lossColor } from "../utils";

export type HeatMetric = "loss" | "avg" | "jitter";

/**
 * Hop-by-run heatmap: each column is one MTR run, each row one hop.
 * Colour encodes loss %, average latency or jitter for that hop in that run.
 */
export function HopHeatmap({ history, metric, rangeSec, onSelectRun }: { history: HopHistory; metric: HeatMetric; rangeSec: number; onSelectRun?: (runId: number) => void }) {
  const [hover, setHover] = useState<{ x: number; y: number; runIdx: number; hop: number } | null>(null);
  const runs = history.runs;
  const rowsCount = history.max_hops;

  const maxLatency = useMemo(() => {
    let m = 0;
    runs.forEach((r) => r.hops.forEach((h) => {
      const v = metric === "jitter" ? h.jitter : h.avg;
      if (v !== null && v > m) m = v;
    }));
    return m || 1;
  }, [runs, metric]);

  if (!runs.length || !rowsCount) {
    return <div className="flex h-40 items-center justify-center text-sm text-faint">No completed runs in this range.</div>;
  }

  const cellW = Math.max(4, Math.min(18, Math.floor(1000 / runs.length)));
  const cellH = 18;
  const labelW = 184;
  const width = labelW + runs.length * (cellW + 1);
  const height = rowsCount * (cellH + 1) + 22;

  const hopNames: string[] = [];
  for (let i = 1; i <= rowsCount; i++) {
    let name = "";
    for (let k = runs.length - 1; k >= 0 && !name; k--) {
      const h = runs[k].hops.find((x) => x.hop === i);
      if (h?.ip) name = h.hostname || h.ip;
    }
    hopNames.push(name || "no response");
  }

  const hovered = hover ? runs[hover.runIdx] : null;
  const hoveredHop = hovered?.hops.find((h) => h.hop === hover?.hop) ?? null;
  const valueOf = (h: { loss: number | null; avg: number | null; jitter: number | null } | undefined) => {
    if (!h) return null;
    return metric === "loss" ? h.loss : metric === "avg" ? h.avg : h.jitter;
  };

  return (
    <div>
      <HeatLegend metric={metric} max={maxLatency} />
      <div className="relative overflow-x-auto">
      <svg width={width} height={height} className="block select-none" onMouseLeave={() => setHover(null)}>
        {Array.from({ length: rowsCount }, (_, i) => (
          <text key={i} x={labelW - 8} y={i * (cellH + 1) + cellH - 3} fontSize={12} textAnchor="end" fill="var(--text-muted)" fontFamily="var(--font-mono)">
            <tspan fill="var(--text-faint)">{String(i + 1).padStart(2, " ")} </tspan>
            {hopNames[i].length > 20 ? `${hopNames[i].slice(0, 19)}…` : hopNames[i]}
          </text>
        ))}
        {runs.map((run, ri) => {
          const x = labelW + ri * (cellW + 1);
          return (
            <g key={run.run_id} onClick={() => onSelectRun?.(run.run_id)} style={{ cursor: onSelectRun ? "pointer" : "default" }}>
              {Array.from({ length: rowsCount }, (_, hi) => {
                const h = run.hops.find((c) => c.hop === hi + 1);
                const v = valueOf(h);
                let fill = "var(--surface-2)";
                if (h) {
                  if (metric === "loss") fill = lossColor(h.loss ?? 100);
                  else fill = h.ip ? latencyColor(v, maxLatency) : "rgba(239,68,68,0.9)";
                }
                return (
                  <rect
                    key={hi}
                    x={x}
                    y={hi * (cellH + 1)}
                    width={cellW}
                    height={cellH}
                    rx={1.5}
                    fill={fill}
                    opacity={hover && hover.runIdx === ri && hover.hop === hi + 1 ? 1 : 0.92}
                    stroke={hover && hover.runIdx === ri && hover.hop === hi + 1 ? "var(--text)" : "none"}
                    onMouseEnter={() => setHover({ x: x + cellW / 2, y: hi * (cellH + 1), runIdx: ri, hop: hi + 1 })}
                  />
                );
              })}
            </g>
          );
        })}
        {runs.length > 1 && [0, Math.floor(runs.length / 2), runs.length - 1].map((ri) => (
          <text key={ri} x={labelW + ri * (cellW + 1) + cellW / 2} y={height - 6} fontSize={12} textAnchor={ri === 0 ? "start" : ri === runs.length - 1 ? "end" : "middle"} fill="var(--text-faint)">
            {fmtTime(runs[ri].t, { date: rangeSec > 86400 })}
          </text>
        ))}
      </svg>
      {hover && hovered && (
        <div className="card pointer-events-none absolute z-10 px-2.5 py-1.5 text-xs" style={{ left: Math.min(hover.x + 10, width - 220), top: hover.y + 18, borderColor: "var(--border-strong)" }}>
          <div className="font-semibold">
            Hop {hover.hop} · {fmtTime(hovered.t, { seconds: true, date: rangeSec > 86400 })}
          </div>
          {hoveredHop ? (
            <div className="num mt-0.5 grid grid-cols-[auto_1fr] gap-x-3">
              <span className="text-muted">Host</span>
              <span className="font-mono">{hoveredHop.ip ? hoveredHop.hostname || hoveredHop.ip : "no response"}</span>
              <span className="text-muted">Loss</span>
              <span>{fmtNum(hoveredHop.loss)}%</span>
              <span className="text-muted">Avg / Best / Worst</span>
              <span>
                {fmtNum(hoveredHop.avg)} / {fmtNum(hoveredHop.best)} / {fmtNum(hoveredHop.worst)} ms
              </span>
              <span className="text-muted">Jitter</span>
              <span>{fmtNum(hoveredHop.jitter)} ms</span>
            </div>
          ) : (
            <div className="text-faint">hop not present in this run</div>
          )}
        </div>
      )}
      </div>
    </div>
  );
}

export function HeatLegend({ metric, max = 1 }: { metric: HeatMetric; max?: number }) {
  const stops = metric === "loss" ? [0, 2, 10, 40, 100] : [0, 0.25, 0.5, 0.75, 1].map((r) => r * max);
  return (
    <div className="mb-3 flex flex-wrap items-end gap-x-3 gap-y-2 text-xs text-muted" aria-label={`${metric === "loss" ? "Loss" : metric === "jitter" ? "Jitter" : "Latency"} scale`}>
      <span className="self-center">{metric === "loss" ? "Loss (%)" : metric === "jitter" ? "Jitter (ms)" : "Latency (ms)"}</span>
      {stops.map((value, index) => <span key={index} className="flex min-w-8 flex-col gap-1 text-center">
        <span className="h-2.5 rounded-sm" style={{ background: metric === "loss" ? lossColor(value) : latencyColor(value, max) }} />
        <span className="num">{fmtNum(value, metric === "loss" ? 0 : 1)}</span>
      </span>)}
      {metric !== "loss" && <span className="ml-1 inline-flex items-center gap-1.5 self-center"><span className="h-2.5 w-4 rounded-sm bg-down" />No response</span>}
      <span className="inline-flex items-center gap-1.5 self-center"><span className="h-2.5 w-4 rounded-sm border border-border bg-surface-2" />No data</span>
    </div>
  );
}
