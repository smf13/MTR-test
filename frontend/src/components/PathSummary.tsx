import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { HopSummary, HopSummaryEntry } from "../api";
import { fmtNum, lossColor } from "../utils";

/** Aggregated per-hop statistics over a time range, including alternate (ECMP / rerouted) hops. */
export function PathSummary({ summary, dstIp }: { summary: HopSummary; dstIp?: string | null }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const maxAvg = useMemo(() => Math.max(1, ...summary.hops.map((h) => h.primary.worst_ms ?? h.primary.avg_ms ?? 0)), [summary]);
  if (!summary.hops.length) return <div className="px-3 py-8 text-center text-sm text-faint">No completed runs in this range.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="table num">
        <thead>
          <tr>
            <th className="w-10 text-right">#</th>
            <th>Host</th>
            <th>ASN</th>
            <th className="text-right" title="Share of runs in which this hop answered from this address">
              Seen
            </th>
            <th className="text-right">Loss</th>
            <th className="text-right">Max loss</th>
            <th className="text-right">Avg</th>
            <th className="text-right">Best</th>
            <th className="text-right">Wrst</th>
            <th className="text-right">StDev</th>
            <th className="text-right">Jitter</th>
            <th className="w-28 min-w-[7rem]">Latency</th>
          </tr>
        </thead>
        <tbody>
          {summary.hops.map((h) => {
            const rows: { entry: HopSummaryEntry; alt: boolean }[] = [{ entry: h.primary, alt: false }];
            const expanded = !!open[h.hop];
            if (expanded) h.alternates.forEach((a) => rows.push({ entry: a, alt: true }));
            return rows.map(({ entry, alt }, i) => (
              <Row key={`${h.hop}-${i}`} hop={h.hop} entry={entry} alt={alt} altCount={h.alternates.length} expanded={expanded} onToggle={() => setOpen((o) => ({ ...o, [h.hop]: !o[h.hop] }))} maxAvg={maxAvg} isDst={!!dstIp && entry.ip === dstIp} />
            ));
          })}
        </tbody>
      </table>
    </div>
  );
}

function Row({ hop, entry, alt, altCount, expanded, onToggle, maxAvg, isDst }: { hop: number; entry: HopSummaryEntry; alt: boolean; altCount: number; expanded: boolean; onToggle: () => void; maxAvg: number; isDst: boolean }) {
  const avg = entry.avg_ms ?? 0;
  const best = entry.best_ms ?? 0;
  const worst = entry.worst_ms ?? 0;
  return (
    <tr style={alt ? { background: "var(--surface-2)" } : isDst ? { background: "var(--accent-soft)" } : undefined}>
      <td className="text-right text-muted">{alt ? "" : hop}</td>
      <td className="font-sans">
        <div className="flex items-center gap-1.5">
          {!alt && altCount > 0 ? (
            <button className="text-faint hover:text-text" onClick={onToggle} title={`${altCount} alternate address${altCount > 1 ? "es" : ""} seen at this hop`}>
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          ) : (
            <span className="inline-block w-[14px]" />
          )}
          <div className="leading-tight">
            <div>
              {entry.ip ? entry.hostname || entry.ip : <span className="italic text-faint">No response</span>}
              {!alt && altCount > 0 && (
                <span className="ml-1.5 rounded px-1 text-[10px] font-semibold" style={{ background: "var(--paused-soft)", color: "var(--text-muted)" }}>
                  +{altCount}
                </span>
              )}
            </div>
            {entry.hostname && entry.ip && <div className="font-mono text-[11px] text-faint">{entry.ip}</div>}
          </div>
        </div>
      </td>
      <td className="font-mono text-xs text-muted">{entry.asn || "–"}</td>
      <td className="text-right text-muted">{entry.share_pct !== null ? `${fmtNum(entry.share_pct, 0)}%` : "–"}</td>
      <td className="text-right">
        <span className="inline-block min-w-[3.2rem] rounded px-1.5 py-0.5 text-xs font-semibold" style={{ background: lossColor(entry.loss_pct, 0.22), color: entry.loss_pct > 0 ? lossColor(entry.loss_pct) : "var(--text-muted)" }}>
          {fmtNum(entry.loss_pct)}%
        </span>
      </td>
      <td className="text-right text-muted">{fmtNum(entry.max_loss_pct)}%</td>
      <td className="text-right font-semibold">{fmtNum(entry.avg_ms)}</td>
      <td className="text-right">{fmtNum(entry.best_ms)}</td>
      <td className="text-right">{fmtNum(entry.worst_ms)}</td>
      <td className="text-right text-muted">{fmtNum(entry.stdev_ms)}</td>
      <td className="text-right text-muted">{fmtNum(entry.jitter_ms)}</td>
      <td>
        {entry.ip && (
          <div className="relative h-3 w-full rounded-sm" style={{ background: alt ? "var(--bg-elev)" : "var(--surface-2)" }}>
            <div className="absolute top-0 h-full rounded-sm" style={{ left: `${(best / maxAvg) * 100}%`, width: `${Math.max(0.5, ((worst - best) / maxAvg) * 100)}%`, background: "var(--accent)", opacity: 0.3 }} />
            <div className="absolute top-0 h-full w-[2px]" style={{ left: `${(avg / maxAvg) * 100}%`, background: "var(--accent)" }} />
          </div>
        )}
      </td>
    </tr>
  );
}
