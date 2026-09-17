import { useMemo } from "react";
import type { Hop } from "../api";
import { useLocalStorage } from "../hooks";
import { Segmented } from "./RangePicker";
import { fmtNum, lossColor, classNames } from "../utils";

/** Full per-hop metrics table for a single MTR run, styled like mtr's report with a latency bar. */
export function HopTable({ hops, dstIp, compact = false }: { hops: Hop[]; dstIp?: string | null; compact?: boolean }) {
  const [view, setView] = useLocalStorage<"compact" | "all">("mtr-tracker.hop-columns", "compact");
  const detailed = !compact && view === "all";
  const maxAvg = useMemo(() => Math.max(1, ...hops.map((h) => h.worst_ms ?? h.avg_ms ?? 0)), [hops]);
  if (!hops.length) return <div className="px-3 py-6 text-center text-sm text-muted">No hop data.</div>;
  return (
    <div>
      {!compact && <div className="flex flex-wrap items-center justify-between gap-2 px-4 pb-3">
        <span className="text-xs text-muted">Hop metrics · milliseconds</span>
        <Segmented value={view} onChange={setView} options={[{ value: "compact", label: "Compact" }, { value: "all", label: "All metrics" }]} />
      </div>}
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Hop metrics table">
      <table className="table hop-table num">
        <thead>
          <tr>
            <th className="w-10 text-right">#</th>
            <th>Host</th>
            {detailed && <th>ASN</th>}
            <th className="text-right">Loss</th>
            {detailed && <th className="text-right">Sent</th>}
            {detailed && <th className="text-right">Received</th>}
            {detailed && <th className="text-right">Last (ms)</th>}
            <th className="text-right">Avg (ms)</th>
            {detailed && <th className="text-right">Best (ms)</th>}
            <th className="text-right">Worst (ms)</th>
            {detailed && <th className="text-right">StDev (ms)</th>}
            <th className="text-right" title="Average jitter (Javg)">
              Jitter (ms)
            </th>
            {detailed && (
              <th className="text-right" title="Maximum jitter (Jmax)">
                Jmax (ms)
              </th>
            )}
            {detailed && <th className="w-28 min-w-[7rem]">Latency (ms)</th>}
          </tr>
        </thead>
        <tbody>
          {hops.map((h) => {
            const isDst = !!dstIp && h.ip === dstIp;
            const noReply = !h.ip;
            const avg = h.avg_ms ?? 0;
            const best = h.best_ms ?? 0;
            const worst = h.worst_ms ?? 0;
            return (
              <tr key={h.hop_no} className={classNames(isDst && "font-medium")} style={isDst ? { background: "var(--accent-soft)" } : undefined}>
                <td className="text-right text-muted">{h.hop_no}</td>
                <td className="font-sans">
                  {noReply ? (
                    <span className="text-faint italic">No response</span>
                  ) : (
                    <div className="leading-tight">
                      <div className="flex items-center gap-1.5">
                        <span className="hop-host" title={h.hostname || h.ip || undefined}>{h.hostname || h.ip}</span>
                        {isDst && (
                          <span className="rounded px-1 py-px text-xs font-semibold uppercase tracking-wide" style={{ background: "var(--accent)", color: "var(--accent-fg)" }}>
                            dst
                          </span>
                        )}
                      </div>
                      {h.hostname && <div className="hop-host font-mono text-xs text-muted" title={h.ip ?? undefined}>{h.ip}</div>}
                    </div>
                  )}
                </td>
                {detailed && <td className="font-mono text-xs text-muted">{h.asn || "–"}</td>}
                <td className="text-right">
                  <span className="inline-block min-w-[3.2rem] rounded px-1.5 py-0.5 text-right text-xs font-semibold" style={{ background: h.loss_pct > 0 ? lossColor(h.loss_pct, 0.22) : "var(--surface-2)", color: h.loss_pct > 0 ? lossColor(h.loss_pct) : "var(--text-muted)" }}>
                    {fmtNum(h.loss_pct, 1)}%
                  </span>
                </td>
                {detailed && <td className="text-right text-muted">{h.sent}</td>}
                {detailed && <td className="text-right text-muted">{h.received}</td>}
                {detailed && <td className="text-right">{fmtNum(h.last_ms)}</td>}
                <td className="text-right font-semibold">{fmtNum(h.avg_ms)}</td>
                {detailed && <td className="text-right">{fmtNum(h.best_ms)}</td>}
                <td className="text-right">{fmtNum(h.worst_ms)}</td>
                {detailed && <td className="text-right text-muted">{fmtNum(h.stdev_ms)}</td>}
                <td className="text-right text-muted">{fmtNum(h.jitter_avg_ms)}</td>
                {detailed && <td className="text-right text-muted">{fmtNum(h.jitter_max_ms)}</td>}
                {detailed && <td>
                  {!noReply && (
                    <div className="relative h-3 w-full rounded-sm" style={{ background: "var(--surface-2)" }} title={`best ${fmtNum(best)} · avg ${fmtNum(avg)} · worst ${fmtNum(worst)} ms`}>
                      <div className="absolute top-0 h-full rounded-sm" style={{ left: `${(best / maxAvg) * 100}%`, width: `${Math.max(0.5, ((worst - best) / maxAvg) * 100)}%`, background: "var(--accent)", opacity: 0.3 }} />
                      <div className="absolute top-0 h-full w-[2px] rounded-sm" style={{ left: `${(avg / maxAvg) * 100}%`, background: "var(--accent)" }} />
                    </div>
                  )}
                </td>}
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </div>
  );
}
