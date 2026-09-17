import { CheckCircle2, XCircle, ShieldAlert, ShieldCheck, ExternalLink } from "lucide-react";
import type { GlobalpingProbe, Run, ProbeType, TlsInfo } from "../api";
import { fmtDateTime, fmtNum, lossColor } from "../utils";

/** Human-friendly rendering of a non-MTR run's `details` (ping samples, HTTP status, TLS, DNS answers, remote probes…). */
export function CheckDetails({ run, type }: { run: Run; type: ProbeType }) {
  const d = (run.details || {}) as Record<string, unknown>;
  const ok = run.status === "ok" && run.reached;
  // The threshold the probe applied is stored with the run, so old runs keep the warning they were judged by.
  const tlsSoon = Number(d.tls_expires_in_days) <= Number(d.tls_warn_days ?? 14);
  const tls = (d.tls && typeof d.tls === "object" ? d.tls : null) as TlsInfo | null;
  const probes = (Array.isArray(d.probes) ? d.probes : []) as GlobalpingProbe[];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {ok ? <CheckCircle2 size={16} style={{ color: "var(--up)" }} /> : <XCircle size={16} style={{ color: "var(--down)" }} />}
        <span className="font-semibold">{ok ? "Check passed" : "Check failed"}</span>
        {run.error && <span className="text-muted">· {run.error}</span>}
      </div>

      {type === "ping" && Array.isArray(d.samples_ms) && <PingSamples samples={d.samples_ms as number[]} sent={Number(d.sent ?? 0)} />}

      <dl className="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
        {type === "http" && (
          <>
            <Row k="Status" v={d.status !== undefined ? <span style={{ color: d.status_ok ? "var(--up)" : "var(--down)" }}>{String(d.status)} {String(d.reason ?? "")}</span> : "–"} />
            <Row k="Response time" v={run.avg_ms !== null ? `${fmtNum(run.avg_ms)} ms` : "–"} />
            <Row k="Size" v={d.bytes !== undefined ? `${(Number(d.bytes) / 1024).toFixed(1)} KB` : "–"} />
            <Row k="Content type" v={String(d.content_type ?? "–")} />
            <Row k="Server" v={String(d.server ?? "–")} />
            <Row k="Protocol" v={String(d.http_version ?? "–")} />
            {d.redirects !== undefined && Number(d.redirects) > 0 && <Row k="Redirects" v={`${d.redirects} → ${d.final_url}`} mono />}
            {d.keyword !== undefined && <Row k="Keyword" v={<span style={{ color: d.keyword_found ? "var(--up)" : "var(--down)" }}>"{String(d.keyword)}" {d.keyword_found ? "found" : "not found"}</span>} />}
            {d.json_path !== undefined && <Row k={`JSON ${String(d.json_path)}`} v={<span style={{ color: d.json_ok === false ? "var(--down)" : d.json_ok ? "var(--up)" : undefined }}>{JSON.stringify(d.json_value)}</span>} mono />}
            {d.tls_expires_in_days !== undefined && (
              <Row
                k="TLS certificate"
                v={
                  d.tls_expires_in_days === null ? (
                    <span className="text-muted">{String(d.tls_error ?? "not checked")}</span>
                  ) : (
                    <span className="inline-flex items-center gap-1" style={{ color: tlsSoon ? "var(--degraded)" : "var(--up)" }}>
                      {tlsSoon ? <ShieldAlert size={13} /> : <ShieldCheck size={13} />} expires in {String(d.tls_expires_in_days)} days
                    </span>
                  )
                }
              />
            )}
            {tls && (
              <>
                <Row k="Issued to" v={<>{tls.subject ?? "–"}{tls.subject_org ? <span className="text-muted"> · {tls.subject_org}</span> : null}</>} />
                <Row k="Issued by" v={<>{tls.issuer ?? "–"}{tls.issuer_cn && tls.issuer_cn !== tls.issuer ? <span className="text-muted"> · {tls.issuer_cn}</span> : null}</>} />
                <Row k="Valid" v={<>{fmtDateTime(tls.not_before)} <span className="text-faint">to</span> <span style={{ color: tlsSoon ? "var(--degraded)" : undefined }}>{fmtDateTime(tls.not_after)}</span></>} />
                <Row k="Protocol" v={`${tls.protocol ?? "–"}${tls.cipher ? ` · ${tls.cipher}` : ""}`} mono />
                {tls.san.length > 0 && <Row k="Names" v={<span className="break-all">{tls.san.slice(0, 8).join(", ")}{tls.san.length > 8 ? ` +${tls.san.length - 8} more` : ""}</span>} mono wide />}
                {tls.serial && <Row k="Serial" v={tls.serial} mono />}
              </>
            )}
          </>
        )}
        {type === "globalping" && (
          <>
            <Row k="Measurement" v={`${String(d.measurement ?? "ping")} from ${String(d.location ?? "world")}`} />
            <Row k="Probes" v={`${probes.length}${probes.length ? ` (${probes.filter((p) => p.status === "finished").length} finished)` : ""}`} />
            <Row k="Loss" v={<span style={{ color: (run.loss_pct ?? 0) > 0 ? lossColor(run.loss_pct) : undefined }}>{fmtNum(run.loss_pct)}%</span>} />
            <Row k="Avg / best / worst" v={`${fmtNum(run.avg_ms)} / ${fmtNum(run.best_ms)} / ${fmtNum(run.worst_ms)} ms`} />
            {typeof d.url === "string" && d.url.startsWith("https://") && (
              <Row k="Report" v={<a className="inline-flex items-center gap-1 text-accent hover:underline" href={d.url} target="_blank" rel="noreferrer">Open on globalping.io <ExternalLink size={12} /></a>} />
            )}
            {d.rate_limit !== undefined && d.rate_limit !== null && typeof d.rate_limit === "object" && "remaining" in (d.rate_limit as object) && (
              <Row k="API quota" v={`${String((d.rate_limit as { remaining?: string }).remaining)} of ${String((d.rate_limit as { limit?: string }).limit ?? "?")} left this hour`} />
            )}
          </>
        )}
        {type === "tcp" && (
          <>
            <Row k="Port" v={String(d.port ?? run.command ?? "–")} mono />
            <Row k="Connect time" v={run.avg_ms !== null ? `${fmtNum(run.avg_ms)} ms` : "–"} />
            <Row k="Connected" v={<span style={{ color: d.connected ? "var(--up)" : "var(--down)" }}>{d.connected ? "yes" : "no"}</span>} />
            <Row k="Address" v={run.dst_ip ?? "–"} mono />
          </>
        )}
        {type === "dns" && (
          <>
            <Row k="Record" v={String(d.record_type ?? "A")} mono />
            {d.random_prefix === true && <Row k="Queried name" v={<>{String(d.queried_name ?? "–")} <span className="font-sans text-faint">(random label, uncached)</span></>} mono wide />}
            <Row k="Resolver" v={String(d.nameserver ?? d.resolver ?? "system")} mono />
            <Row k="Lookup time" v={run.avg_ms !== null ? `${fmtNum(run.avg_ms)} ms` : "–"} />
            {d.rcode !== undefined && <Row k="Result" v={<>{String(d.rcode)}{d.rcode === "NXDOMAIN" && d.random_prefix === true ? <span className="text-faint"> · expected for a random label; the timing is the measurement</span> : null}</>} mono={false} />}
            <Row k="TTL" v={d.ttl !== undefined && d.ttl !== null ? `${d.ttl}s` : "–"} />
            {Array.isArray(d.answers) && <Row k="Answers" v={<span className="whitespace-pre-wrap break-all">{(d.answers as string[]).join("\n") || "(none)"}</span>} mono wide />}
          </>
        )}
        {type === "ping" && (
          <>
            <Row k="Sent / received" v={`${d.sent ?? run.sent ?? "–"} / ${d.received ?? "–"}`} />
            <Row k="Loss" v={<span style={{ color: (run.loss_pct ?? 0) > 0 ? lossColor(run.loss_pct) : undefined }}>{fmtNum(run.loss_pct)}%</span>} />
            <Row k="Avg / best / worst" v={`${fmtNum(run.avg_ms)} / ${fmtNum(run.best_ms)} / ${fmtNum(run.worst_ms)} ms`} />
            <Row k="StDev / jitter" v={`${fmtNum(run.stdev_ms)} / ${fmtNum(run.jitter_avg_ms)} ms`} />
            <Row k="Address" v={run.dst_ip ?? "–"} mono />
            <Row k="Packet size" v={d.packet_size !== undefined ? `${d.packet_size} B` : "–"} />
          </>
        )}
      </dl>
      {type === "globalping" && probes.length > 0 && <ProbeTable probes={probes} />}
      {run.command && <div className="font-mono text-[11px] text-faint">{run.command}</div>}
    </div>
  );
}

function ProbeTable({ probes }: { probes: GlobalpingProbe[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="table num">
        <thead>
          <tr>
            <th>Probe</th>
            <th>Resolved</th>
            <th className="text-right">Sent / rcvd</th>
            <th className="text-right">Loss</th>
            <th className="text-right">Min</th>
            <th className="text-right">Avg</th>
            <th className="text-right">Max</th>
          </tr>
        </thead>
        <tbody>
          {probes.map((p, i) => (
            <tr key={i}>
              <td className="font-sans">{p.label}{p.status && p.status !== "finished" ? <span className="ml-1.5 text-[11px] text-down">{p.status}</span> : null}</td>
              <td className="font-mono text-xs text-muted">{p.resolved ?? "–"}</td>
              <td className="text-right text-muted">{p.sent ?? "–"} / {p.received ?? "–"}</td>
              <td className="text-right" style={{ color: (p.loss ?? 0) > 0 ? lossColor(p.loss) : undefined }}>{fmtNum(p.loss)}%</td>
              <td className="text-right">{fmtNum(p.min)}</td>
              <td className="text-right font-semibold">{fmtNum(p.avg)}</td>
              <td className="text-right">{fmtNum(p.max)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({ k, v, mono, wide }: { k: string; v: React.ReactNode; mono?: boolean; wide?: boolean }) {
  // A wide row starts a fresh line so its value can span the remaining three columns instead of wrapping under the label.
  return (
    <>
      <dt className={`text-muted ${wide ? "sm:col-start-1" : ""}`}>{k}</dt>
      <dd className={`${mono ? "font-mono text-xs" : ""} ${wide ? "sm:col-span-3" : ""} min-w-0 break-words`}>{v}</dd>
    </>
  );
}

function PingSamples({ samples, sent }: { samples: number[]; sent: number }) {
  const max = Math.max(1, ...samples);
  const lost = Math.max(0, sent - samples.length);
  return (
    <div>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-faint">Round-trip per ping</div>
      <div className="flex h-16 items-end gap-[3px]">
        {samples.map((s, i) => (
          <div key={i} className="flex-1 rounded-sm" style={{ height: `${Math.max(4, (s / max) * 100)}%`, background: "var(--chart-avg)", opacity: 0.85 }} title={`#${i + 1}: ${fmtNum(s, 2)} ms`} />
        ))}
        {Array.from({ length: lost }, (_, i) => (
          <div key={`lost-${i}`} className="flex-1 rounded-sm" style={{ height: "100%", background: "var(--down)", opacity: 0.5 }} title="lost" />
        ))}
      </div>
    </div>
  );
}
