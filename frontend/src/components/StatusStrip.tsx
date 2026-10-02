import type { TimelineBucket } from "../api";
import { fmtNum, fmtTime, classNames } from "../utils";

/* Status timeline strip (one cell per bucket; 24h on the dashboard, the selected range on a target page). Plain divs: no charting library, so the dashboard's
   first paint does not have to wait for it. */

const STATUS_FILL: Record<TimelineBucket["s"], string> = { up: "var(--up)", degraded: "var(--degraded)", down: "var(--down)" };

export function StatusStrip({ buckets, bucketSec, since, height = 8, className, label = "Status over the last 24 hours" }: { buckets: (TimelineBucket | null)[]; bucketSec: number; since: string; height?: number; className?: string; label?: string }) {
  const start = new Date(since).getTime();
  // Past a day the cells span dates, so their tooltips name the day too.
  const withDate = buckets.length * bucketSec > 86400;
  return (
    <div className={classNames("flex w-full gap-px", className)} style={{ height }} aria-label={label}>
      {buckets.map((b, i) => {
        const from = new Date(start + i * bucketSec * 1000);
        const title = b
          ? `${fmtTime(from.getTime(), { date: withDate })}: ${b.s}${b.avg !== null ? ` · avg ${fmtNum(b.avg)} ms` : ""} · ${b.n} run${b.n === 1 ? "" : "s"}`
          : `${fmtTime(from.getTime(), { date: withDate })}: no runs`;
        return <div key={i} className="flex-1 rounded-[1px]" style={{ background: b ? STATUS_FILL[b.s] : "var(--surface-2)", opacity: b ? 0.9 : 1 }} title={title} />;
      })}
    </div>
  );
}
