import { useId, useMemo } from "react";
import type { SparkPoint } from "../api";

/** Compact latency sparkline with loss/failed runs marked underneath. */
export function Sparkline({ points, width = 160, height = 36, color = "var(--chart-avg)", fluid = false }: { points: SparkPoint[]; width?: number; height?: number; color?: string; fluid?: boolean }) {
  const gradId = `sparkline-${useId().replace(/:/g, "")}`;
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
    let segment = "";
    let segmentStart = 0;
    let segmentEnd = 0;
    const areas: string[] = [];
    const closeSegment = () => {
      if (segment) areas.push(`${segment} L${segmentEnd.toFixed(1)},${plotH} L${segmentStart.toFixed(1)},${plotH} Z`);
      segment = "";
    };
    vals.forEach((v, i) => {
      if (v === null) {
        closeSegment();
        started = false;
        return;
      }
      const x = i * stepX;
      const point = `${x.toFixed(1)},${y(v).toFixed(1)}`;
      d += started ? ` L${point}` : `M${point}`;
      if (!started) segmentStart = x;
      segment += started ? ` L${point}` : `M${point}`;
      segmentEnd = x;
      started = true;
    });
    const marks = points
      .map((p, i) => ({ x: i * stepX, loss: !p.reached ? 100 : (p.loss ?? 0) }))
      .filter((m) => m.loss > 0);
    closeSegment();
    const areaPath = areas.join(" ");
    return { path: d, area: areaPath, marks };
  }, [points, width, height]);

  if (!points.length) {
    return (
      <svg width={fluid ? "100%" : width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="block" role="img" aria-label="Latency trend: no data yet">
        <text x={0} y={height / 2 + 4} fontSize={10} fill="var(--text-faint)">
          no data yet
        </text>
      </svg>
    );
  }
  return (
    <svg width={fluid ? "100%" : width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="block overflow-hidden" role="img" aria-label="Recent latency trend with loss markers">
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.35} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {area && <path d={area} fill={`url(#${gradId})`} />}
      {path && <path d={path} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />}
      {marks.map((m, i) => (
        <rect key={i} x={m.x - 1} y={height - 4} width={2} height={4} rx={0.5} fill={m.loss >= 100 ? "var(--down)" : "var(--degraded)"} />
      ))}
    </svg>
  );
}
