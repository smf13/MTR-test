import { GitBranch, ExternalLink } from "lucide-react";
import type { Run } from "../api";
import { fmtDateTime, fmtNum, lossColor, relTime } from "../utils";

export function RunsTable({ runs, onOpen, now }: { runs: Run[]; onOpen: (id: number) => void; now: number }) {
  if (!runs.length) return <div className="px-3 py-8 text-center text-sm text-faint">No runs match.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="table num">
        <thead>
          <tr>
            <th>Started</th>
            <th>Result</th>
            <th className="text-right">Hops</th>
            <th className="text-right">Loss</th>
            <th className="text-right">Avg</th>
            <th className="text-right">Best</th>
            <th className="text-right">Wrst</th>
            <th className="text-right">StDev</th>
            <th className="text-right">Jitter</th>
            <th className="text-right">Duration</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const ok = r.status === "ok" && r.reached;
            return (
              <tr key={r.id} className="cursor-pointer" onClick={() => onOpen(r.id)}>
                <td>
                  <div className="leading-tight">
                    <div>{fmtDateTime(r.started_at)}</div>
                    <div className="text-xs text-faint">{relTime(r.started_at, now)}</div>
                  </div>
                </td>
                <td className="font-sans">
                  <span className="inline-flex items-center gap-1.5 text-xs font-semibold" style={{ color: ok ? "var(--up)" : "var(--down)" }}>
                    <span className="inline-block h-2 w-2 rounded-full" style={{ background: ok ? "var(--up)" : "var(--down)" }} />
                    {r.status !== "ok" ? "Error" : r.reached ? "Reached" : "Unreachable"}
                  </span>
                  {r.route_changed && (
                    <span className="ml-2 inline-flex items-center gap-1 text-xs" style={{ color: "var(--chart-jitter)" }}>
                      <GitBranch size={11} /> route change
                    </span>
                  )}
                  {r.error && <div className="max-w-[320px] truncate text-xs text-faint" title={r.error}>{r.error}</div>}
                </td>
                <td className="text-right">{r.hop_count || "–"}</td>
                <td className="text-right">
                  <span className="font-semibold" style={{ color: (r.loss_pct ?? 0) > 0 ? lossColor(r.loss_pct) : undefined }}>
                    {fmtNum(r.loss_pct)}%
                  </span>
                </td>
                <td className="text-right font-semibold">{fmtNum(r.avg_ms)}</td>
                <td className="text-right">{fmtNum(r.best_ms)}</td>
                <td className="text-right">{fmtNum(r.worst_ms)}</td>
                <td className="text-right text-muted">{fmtNum(r.stdev_ms)}</td>
                <td className="text-right text-muted">{fmtNum(r.jitter_avg_ms)}</td>
                <td className="text-right text-muted">{r.duration_ms !== null ? `${(r.duration_ms / 1000).toFixed(1)}s` : "–"}</td>
                <td className="text-faint">
                  <ExternalLink size={13} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
