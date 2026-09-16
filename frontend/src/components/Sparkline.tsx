import { useMemo } from "react";
import type { SparkPoint } from "../api";

/** Compact latency sparkline with loss/failed runs marked underneath. */
export function Sparkline({ points, width = 160, height = 36, color = "var(--accent)" }: { points: SparkPoint[]; width?: number; height?: number; color?: string }) {
  const { path, area, marks } = useMemo(() => {
    const vals = points.map((p) => (p.reached && p.avg !== null ? p.avg : null));
    const nums = vals.filter((v): v is number => v !== null);
    if (!nums.length) return { path: "", area: "", marks: [] as { x: number; loss: number }[] };
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const span = max - min || 1;
    const n = points.length;
    const stepX = n > 1 ? width / (n - 1) : 0;
    const topPad = 3;
    const plotH = height - 8;
    const y = (v: number) => topPad + (1 - (v - min) / span) * (plotH - topPad);
    let d = "";
    let started = false;
    vals.forEach((v, i) => {
      if (v === null) {
        started = false;
        return;
      }
      const x = i * stepX;
      d += started ? ` L${x.toFixed(1)},${y(v).toFixed(1)}` : `M${x.toFixed(1)},${y(v).toFixed(1)}`;
      started = true;
    });
    const marks = points
      .map((p, i) => ({ x: i * stepX, loss: !p.reached ? 100 : (p.loss ?? 0) }))
      .filter((m) => m.loss > 0);
    const areaPath = d ? `${d} L${((n - 1) * stepX).toFixed(1)},${plotH} L0,${plotH} Z` : "";
    return { path: d, area: areaPath, marks };
  }, [points, width, height]);

  if (!points.length) {
    return (
      <svg width={width} height={height} className="block">
        <text x={0} y={height / 2 + 4} fontSize={10} fill="var(--text-faint)">
          no data yet
        </text>
      </svg>
    );
  }
  const gradId = `sp-${Math.abs(hash(color))}`;
  return (
    <svg width={width} height={height} className="block overflow-visible">
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.35} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {area && <path d={area} fill={`url(#${gradId})`} />}
      {path && <path d={path} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />}
      {marks.map((m, i) => (
        <rect key={i} x={m.x - 1} y={height - 4} width={2} height={4} rx={0.5} fill={m.loss >= 100 ? "var(--down)" : "var(--degraded)"} />
      ))}
    </svg>
  );
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}
