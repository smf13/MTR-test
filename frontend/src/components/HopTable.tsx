import { useMemo } from "react";
import type { Hop } from "../api";
import { fmtNum, lossColor, classNames } from "../utils";

/** Full per-hop metrics table for a single MTR run, styled like mtr's report with a latency bar. */
export function HopTable({ hops, dstIp, compact = false }: { hops: Hop[]; dstIp?: string | null; compact?: boolean }) {
  const maxAvg = useMemo(() => Math.max(1, ...hops.map((h) => h.worst_ms ?? h.avg_ms ?? 0)), [hops]);
  if (!hops.length) return <div className="px-3 py-6 text-center text-sm text-muted">No hop data.</div>;
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Hop metrics table">
      <table className="table num">
        <thead>
          <tr>
            <th className="w-10 text-right">#</th>
            <th>Host</th>
            {!compact && <th>ASN</th>}
            <th className="text-right">Loss</th>
            <th className="text-right">Snt</th>
            {!compact && <th className="text-right">Rcv</th>}
            <th className="text-right">Last</th>
            <th className="text-right">Avg</th>
            <th className="text-right">Best</th>
            <th className="text-right">Wrst</th>
            <th className="text-right">StDev</th>
            <th className="text-right" title="Average jitter (Javg)">
              Jitter
            </th>
            {!compact && (
              <th className="text-right" title="Maximum jitter (Jmax)">
                Jmax
              </th>
            )}
            <th className="w-28 min-w-[7rem]">Latency</th>
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
                        <span>{h.hostname || h.ip}</span>
                        {isDst && (
                          <span className="rounded px-1 py-px text-xs font-semibold uppercase tracking-wide" style={{ background: "var(--accent)", color: "var(--accent-fg)" }}>
                            dst
                          </span>
                        )}
                      </div>
                      {h.hostname && <div className="font-mono text-xs text-muted">{h.ip}</div>}
                    </div>
                  )}
                </td>
                {!compact && <td className="font-mono text-xs text-muted">{h.asn || "–"}</td>}
                <td className="text-right">
                  <span className="inline-block min-w-[3.2rem] rounded px-1.5 py-0.5 text-right text-xs font-semibold" style={{ background: h.loss_pct > 0 ? lossColor(h.loss_pct, 0.22) : "var(--surface-2)", color: h.loss_pct > 0 ? lossColor(h.loss_pct) : "var(--text-muted)" }}>
                    {fmtNum(h.loss_pct, 1)}%
                  </span>
                </td>
                <td className="text-right text-muted">{h.sent}</td>
                {!compact && <td className="text-right text-muted">{h.received}</td>}
                <td className="text-right">{fmtNum(h.last_ms)}</td>
                <td className="text-right font-semibold">{fmtNum(h.avg_ms)}</td>
                <td className="text-right">{fmtNum(h.best_ms)}</td>
                <td className="text-right">{fmtNum(h.worst_ms)}</td>
                <td className="text-right text-muted">{fmtNum(h.stdev_ms)}</td>
                <td className="text-right text-muted">{fmtNum(h.jitter_avg_ms)}</td>
                {!compact && <td className="text-right text-muted">{fmtNum(h.jitter_max_ms)}</td>}
                <td>
                  {!noReply && (
                    <div className="relative h-3 w-full rounded-sm" style={{ background: "var(--surface-2)" }} title={`best ${fmtNum(best)} · avg ${fmtNum(avg)} · worst ${fmtNum(worst)} ms`}>
                      <div className="absolute top-0 h-full rounded-sm" style={{ left: `${(best / maxAvg) * 100}%`, width: `${Math.max(0.5, ((worst - best) / maxAvg) * 100)}%`, background: "var(--accent)", opacity: 0.3 }} />
                      <div className="absolute top-0 h-full w-[2px] rounded-sm" style={{ left: `${(avg / maxAvg) * 100}%`, background: "var(--accent)" }} />
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
