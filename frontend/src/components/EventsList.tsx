import { Link } from "react-router-dom";
import { AlertOctagon, AlertTriangle, CheckCircle2, GitBranch, Info } from "lucide-react";
import type { Event } from "../api";
import { fmtDateTime, relTime } from "../utils";

export function EventIcon({ kind, size = 16 }: { kind: string; size?: number }) {
  switch (kind) {
    case "down":
      return <AlertOctagon size={size} style={{ color: "var(--down)" }} />;
    case "degraded":
      return <AlertTriangle size={size} style={{ color: "var(--degraded)" }} />;
    case "recovered":
      return <CheckCircle2 size={size} style={{ color: "var(--up)" }} />;
    case "route_change":
      return <GitBranch size={size} style={{ color: "var(--chart-jitter)" }} />;
    default:
      return <Info size={size} style={{ color: "var(--accent)" }} />;
  }
}

export const EVENT_KIND_LABEL: Record<string, string> = {
  down: "Down",
  degraded: "Degraded",
  recovered: "Recovered",
  route_change: "Route change",
};

export function EventsList({ events, now, showTarget = true }: { events: Event[]; now: number; showTarget?: boolean }) {
  if (!events.length) return <div className="px-3 py-8 text-center text-sm text-faint">No events.</div>;
  return (
    <ul className="divide-y divide-border">
      {events.map((e) => (
        <li key={e.id} className="flex items-start gap-3 px-4 py-3">
          <div className="mt-0.5 shrink-0">
            <EventIcon kind={e.kind} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm leading-snug">
              {showTarget && e.target_id && e.target_name && (
                <Link to={`/targets/${e.target_id}`} className="mr-1.5 font-semibold text-accent hover:underline">
                  {e.target_name}
                </Link>
              )}
              <span>{e.message}</span>
            </div>
            <RouteDiff details={e.details} />
            <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11px] text-faint">
              <span title={fmtDateTime(e.created_at)}>{relTime(e.created_at, now)}</span>
              <span>{fmtDateTime(e.created_at)}</span>
              {e.run_id && (
                <Link to={`/runs/${e.run_id}`} className="text-accent hover:underline">
                  view run #{e.run_id}
                </Link>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function RouteDiff({ details }: { details: Record<string, unknown> | null }) {
  if (!details) return null;
  const prev = details.previous;
  const cur = details.current;
  if (!Array.isArray(prev) || !Array.isArray(cur)) return null;
  const n = Math.max(prev.length, cur.length);
  const changed = Array.from({ length: n }, (_, i) => (prev[i] ?? null) !== (cur[i] ?? null)).filter(Boolean).length;
  return (
    <details className="mt-1 text-[11px]">
      <summary className="cursor-pointer text-faint hover:text-text">
        {changed} hop{changed === 1 ? "" : "s"} differ · show path diff
      </summary>
    <div className="mt-1.5 overflow-x-auto rounded-md border border-border text-[11px]" style={{ background: "var(--bg-elev)" }}>
      <table className="num">
        <tbody>
          {Array.from({ length: n }, (_, i) => {
            const a = (prev[i] as string | null) ?? null;
            const b = (cur[i] as string | null) ?? null;
            const changed = a !== b;
            return (
              <tr key={i}>
                <td className="px-2 py-0.5 text-right text-faint">{i + 1}</td>
                <td className="px-2 py-0.5 font-mono" style={{ color: changed ? "var(--down)" : "var(--text-muted)", textDecoration: changed && a ? "line-through" : undefined }}>
                  {a ?? (i < prev.length ? "???" : "")}
                </td>
                <td className="px-1 text-faint">→</td>
                <td className="px-2 py-0.5 font-mono" style={{ color: changed ? "var(--up)" : "var(--text-muted)" }}>
                  {b ?? (i < cur.length ? "???" : "")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </details>
  );
}
