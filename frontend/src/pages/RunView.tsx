import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronLeft, ChevronRight, Download, GitBranch, Terminal } from "lucide-react";
import { api, PROBE_TYPE_LABEL } from "../api";
import { usePoll } from "../hooks";
import { HopTable } from "../components/HopTable";
import { StatTile } from "../components/StatTile";
import { ErrorBanner } from "../components/EmptyState";
import { fmtDateTime, fmtNum, fmtPct } from "../utils";
import { CheckDetails } from "../components/CheckDetails";

export function RunView() {
  const { id } = useParams();
  const runId = Number(id);
  const navigate = useNavigate();
  const run = usePoll(() => api.run(runId), 60000, [runId]);
  const r = run.data;

  if (run.error && !r) return <ErrorBanner message={run.error} />;
  if (!r) return <div className="py-20 text-center text-sm text-faint">Loading…</div>;
  const ok = r.status === "ok" && r.reached;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to={`/targets/${r.target_id}`} className="inline-flex items-center gap-1 text-xs text-muted hover:text-text">
            <ArrowLeft size={13} /> {r.target_name}
          </Link>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">
            Run #{r.id} <span className="text-base font-normal text-muted">· {fmtDateTime(r.started_at)}</span>
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 text-sm text-muted">
            <span className="font-mono">{r.target_host}{r.dst_ip && r.dst_ip !== r.target_host ? ` → ${r.dst_ip}` : ""}</span>
            {r.src && <span>from {r.src}</span>}
            <span>{r.duration_ms !== null ? `${(r.duration_ms / 1000).toFixed(1)}s` : ""}</span>
            {r.route_changed && <span className="inline-flex items-center gap-1" style={{ color: "var(--chart-jitter)" }}><GitBranch size={13} /> route changed in this run</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn" disabled={!r.prev_run_id} onClick={() => r.prev_run_id && navigate(`/runs/${r.prev_run_id}`)}><ChevronLeft size={15} /> Older</button>
          <button className="btn" disabled={!r.next_run_id} onClick={() => r.next_run_id && navigate(`/runs/${r.next_run_id}`)}>Newer <ChevronRight size={15} /></button>
          <a className="btn" href={api.runReportUrl(r.id)} target="_blank" rel="noreferrer"><Download size={15} /> Text report</a>
        </div>
      </div>

      {r.status !== "ok" && <ErrorBanner message={`Run failed: ${r.error ?? "unknown error"}`} />}
      {r.status === "ok" && !r.reached && <ErrorBanner message="The destination did not respond to any probe in this run." />}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Result" value={r.status !== "ok" ? "Error" : r.reached ? "Reached" : "Unreachable"} tone={ok ? "up" : "down"} />
        <StatTile label={r.hop_count ? "Hops" : "Probe"} value={r.hop_count || PROBE_TYPE_LABEL[r.target_type]} />
        <StatTile label="Loss (dst)" value={fmtPct(r.loss_pct)} tone={(r.loss_pct ?? 0) > 0 ? "degraded" : undefined} />
        <StatTile label="Avg" value={ok ? `${fmtNum(r.avg_ms)} ms` : "–"} sub={ok ? `last ${fmtNum(r.last_ms)} ms` : undefined} />
        <StatTile label="Best / Worst" value={ok ? `${fmtNum(r.best_ms)} / ${fmtNum(r.worst_ms)}` : "–"} sub="ms" />
        <StatTile label="StDev / Jitter" value={ok ? `${fmtNum(r.stdev_ms)} / ${fmtNum(r.jitter_avg_ms)}` : "–"} sub={ok ? `jitter max ${fmtNum(r.jitter_max_ms)} ms` : undefined} />
      </div>

      {r.target_type === "mtr" || r.hop_count > 0 ? (
        <div className="card overflow-hidden">
          <div className="border-b border-border px-4 py-2.5 text-sm font-semibold">All hops</div>
          <HopTable hops={r.hops} dstIp={r.dst_ip} />
        </div>
      ) : (
        <div className="card p-4">
          <div className="mb-2 text-sm font-semibold">Check details</div>
          <CheckDetails run={r} type={r.target_type} />
        </div>
      )}

      {r.command && (
        <div className="card p-4">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-muted"><Terminal size={13} /> Command</div>
          <code className="block overflow-x-auto whitespace-pre font-mono text-xs text-muted">{r.command}</code>
        </div>
      )}
    </div>
  );
}
