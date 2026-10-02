import { Link } from "react-router-dom";
import type { StatusHistory } from "../api";
import { StatusBadge } from "./StatusBadge";
import { STATUS_LABEL, fmtDateTime, fmtDuration, fmtTime, statusColor } from "../utils";

const ORDER = ["up", "degraded", "down"] as const;

/** The target's up / degraded / down periods over the selected range: time per status, then one row per period. */
export function StatusHistoryPanel({ data, range }: { data: StatusHistory | null; range: string }) {
  if (!data) return <div className="py-10 text-center text-sm text-faint">Loading…</div>;
  if (!data.start) return <div className="px-3 py-8 text-center text-sm text-faint">No runs in this range yet.</div>;
  const covered = ORDER.reduce((sum, st) => sum + data.totals[st], 0);
  const lateStart = Date.parse(data.start) - Date.parse(data.since) > 60_000;
  return (
    <div>
      <div className="space-y-2 px-4 py-3">
        <div className="flex h-2.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
          {covered > 0 && ORDER.map((st) => (data.totals[st] > 0 ? <div key={st} style={{ width: `${(100 * data.totals[st]) / covered}%`, background: statusColor(st) }} /> : null))}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" data-testid="status-totals">
          {ORDER.map((st) => (
            <span key={st} className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: statusColor(st) }} aria-hidden />
              <span className="font-medium text-text">{STATUS_LABEL[st]}</span>
              {covered > 0 ? `${fmtPctShare(data.totals[st], covered)} · ${fmtDuration(data.totals[st])}` : "–"}
            </span>
          ))}
          <span>{data.changes} status change{data.changes === 1 ? "" : "s"} · {range}</span>
        </div>
        {(lateStart || data.paused || data.truncated) && (
          <p className="text-xs text-faint">
            {lateStart && <>Covers the time since the first run, {fmtDateTime(data.start)}. </>}
            {data.paused && <>The target is paused; the last period ends when it is resumed and judged again. </>}
            {data.truncated && <>Only the latest {data.periods.length} periods are listed; the totals cover the whole range.</>}
          </p>
        )}
      </div>
      <ul className="divide-y divide-border border-t border-border">
        {data.periods.map((p) => (
          <li key={`${p.start}-${p.status}`} className="flex items-start gap-3 px-4 py-3" data-testid="status-period">
            <StatusBadge status={p.status} className="mt-0.5 shrink-0" />
            <div className="min-w-0 flex-1 text-sm">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="num font-semibold">{fmtDuration(p.duration_sec)}{p.ongoing ? " so far" : ""}</span>
                <span className="text-xs text-muted">
                  <span title={fmtDateTime(p.start)}>{fmtTime(p.start, { date: true })}</span> → {p.ongoing ? "now" : <span title={fmtDateTime(p.end)}>{fmtTime(p.end, { date: true })}</span>}
                </span>
              </div>
              {p.message && <div className="mt-0.5 break-words text-xs text-muted">{p.message}</div>}
              {!p.message && <div className="mt-0.5 text-xs text-faint">Status when the range begins</div>}
            </div>
            {p.run_id !== null && (
              <Link to={`/runs/${p.run_id}`} className="shrink-0 text-xs text-accent hover:underline">Run #{p.run_id}</Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function fmtPctShare(part: number, whole: number): string {
  const pct = (100 * part) / whole;
  if (pct === 0 || pct === 100) return `${pct}%`;
  return `${pct < 0.01 ? "<0.01" : pct > 99.99 ? ">99.99" : pct.toFixed(2)}%`;
}
