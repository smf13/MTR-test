import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Play, Pause, Pencil, Trash2, Clock, GitBranch, Activity, Percent, Gauge, Timer, Route, RefreshCw, Download } from "lucide-react";
import { api, type Target, type TargetInput, type Run } from "../api";
import { usePoll, useNow, useLocalStorage } from "../hooks";
import { StatusBadge } from "../components/StatusBadge";
import { StatTile } from "../components/StatTile";
import { RangePicker, Segmented } from "../components/RangePicker";
import { LatencyChart, LossChart, JitterChart } from "../components/Charts";
import { HopTable } from "../components/HopTable";
import { HopHeatmap, HeatLegend, type HeatMetric } from "../components/HopHeatmap";
import { PathSummary } from "../components/PathSummary";
import { RunsTable } from "../components/RunsTable";
import { EventsList } from "../components/EventsList";
import { TargetForm } from "../components/TargetForm";
import { ConfirmDialog } from "../components/Modal";
import { ErrorBanner } from "../components/EmptyState";
import { useToast } from "../components/Toast";
import { effectiveStatus, fmtDuration, fmtNum, fmtPct, relTime, fmtDateTime, classNames } from "../utils";

type Tab = "path" | "history" | "summary" | "runs" | "events";
const RUN_PAGE = 25;

export function TargetDetail() {
  const { id } = useParams();
  const targetId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const now = useNow();
  const [range, setRange] = useLocalStorage("hopwatch.range", "24h");
  const [tab, setTab] = useLocalStorage<Tab>("hopwatch.tab", "path");
  const [heatMetric, setHeatMetric] = useState<HeatMetric>("loss");
  const [runFilter, setRunFilter] = useState<"" | "ok" | "failed" | "route_change">("");
  const [runPage, setRunPage] = useState(0);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);

  const target = usePoll(() => api.target(targetId, range), 10000, [targetId, range]);
  const pollMs = useMemo(() => Math.min(60000, Math.max(10000, (target.data?.interval_sec ?? 60) * 1000 * 0.5)), [target.data?.interval_sec]);
  const series = usePoll(() => api.series(targetId, range), pollMs, [targetId, range]);
  const latest = usePoll(async () => {
    const r = await api.runs(targetId, { limit: 1 });
    return r.items[0] ? api.run(r.items[0].id) : null;
  }, pollMs, [targetId]);
  const history = usePoll(() => api.hopHistory(targetId, range), pollMs, [targetId, range]);
  const summary = usePoll(() => api.hopSummary(targetId, range), pollMs, [targetId, range]);
  const runs = usePoll(() => api.runs(targetId, { limit: RUN_PAGE, offset: runPage * RUN_PAGE, range, status: runFilter || undefined }), pollMs, [targetId, range, runFilter, runPage]);
  const events = usePoll(() => api.targetEvents(targetId, range), pollMs, [targetId, range]);

  useEffect(() => setRunPage(0), [range, runFilter]);

  const refreshAll = useCallback(() => {
    void target.refresh();
    void series.refresh();
    void latest.refresh();
    void history.refresh();
    void summary.refresh();
    void runs.refresh();
    void events.refresh();
  }, [target, series, latest, history, summary, runs, events]);

  const t: Target | null = target.data;
  const stats = t?.stats;
  const status = t ? effectiveStatus(t) : "pending";

  const save = async (values: TargetInput) => {
    setSaving(true);
    try {
      await api.updateTarget(targetId, values);
      toast("Target updated", "success");
      setEditing(false);
      refreshAll();
    } finally {
      setSaving(false);
    }
  };

  const toggle = async () => {
    if (!t) return;
    await api.updateTarget(t.id, { enabled: !t.enabled });
    toast(t.enabled ? "Monitoring paused" : "Monitoring resumed", "info");
    void target.refresh();
  };

  const runNow = async () => {
    const r = await api.runNow(targetId);
    toast(r.already_running ? "A probe is already running" : "Probe started", "info");
    window.setTimeout(refreshAll, 2500);
  };

  const remove = async () => {
    await api.deleteTarget(targetId);
    toast("Target deleted", "info");
    navigate("/");
  };

  if (target.error && !t) {
    return (
      <div className="space-y-4">
        <Link to="/" className="inline-flex items-center gap-1 text-sm text-muted hover:text-text">
          <ArrowLeft size={15} /> Dashboard
        </Link>
        <ErrorBanner message={target.error} />
      </div>
    );
  }
  if (!t) return <div className="py-20 text-center text-sm text-faint">Loading…</div>;

  const run = t.latest_run;
  const latestRun = latest.data && latest.data.id === run?.id ? latest.data : latest.data;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/" className="inline-flex items-center gap-1 text-xs text-muted hover:text-text">
            <ArrowLeft size={13} /> Dashboard
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight">{t.name}</h1>
            <StatusBadge status={status} running={t.running} />
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <span className="font-mono">{t.host}</span>
            {run?.dst_ip && run.dst_ip !== t.host && <span className="font-mono text-faint">→ {run.dst_ip}</span>}
            <span className="uppercase">{t.protocol}{t.port ? ` :${t.port}` : ""}</span>
            <span className="inline-flex items-center gap-1"><Clock size={12} /> every {fmtDuration(t.interval_sec)}</span>
            <span>{t.count} probes × {t.probe_interval}s</span>
            {t.ip_version !== "auto" && <span>IPv{t.ip_version}</span>}
            {t.tags.map((tag) => (
              <span key={tag} className="rounded px-1.5 py-px text-xs font-medium" style={{ background: "var(--paused-soft)", color: "var(--text-muted)" }}>{tag}</span>
            ))}
          </div>
          {t.description && <p className="mt-1 max-w-2xl text-sm text-muted">{t.description}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn" onClick={runNow} disabled={t.running}><Play size={15} /> Run now</button>
          <button className="btn" onClick={toggle}>{t.enabled ? <><Pause size={15} /> Pause</> : <><Play size={15} /> Resume</>}</button>
          <button className="btn" onClick={() => setEditing(true)}><Pencil size={15} /> Edit</button>
          <button className="btn btn-danger" onClick={() => setDeleting(true)}><Trash2 size={15} /></button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <RangePicker value={range} onChange={setRange} />
        <div className="flex items-center gap-2 text-xs text-faint">
          {run && <span>Last run {relTime(run.started_at, now)}{t.next_run_at && t.enabled ? ` · next ${relTime(t.next_run_at, now).replace("ago", "").trim()}` : ""}</span>}
          <button className="btn btn-ghost btn-sm" onClick={refreshAll} title="Refresh"><RefreshCw size={13} /></button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Latency now" value={run?.reached ? `${fmtNum(run.avg_ms)} ms` : run ? "unreachable" : "–"} sub={run?.reached ? `best ${fmtNum(run.best_ms)} · worst ${fmtNum(run.worst_ms)}` : run?.error ?? undefined} tone={status === "down" ? "down" : status === "degraded" ? "degraded" : undefined} icon={<Gauge size={15} />} />
        <StatTile label={`Avg · ${range}`} value={stats?.avg_ms !== null && stats?.avg_ms !== undefined ? `${fmtNum(stats.avg_ms)} ms` : "–"} sub={stats?.p95_ms !== null && stats?.p95_ms !== undefined ? `p95 ${fmtNum(stats.p95_ms)} · p99 ${fmtNum(stats.p99_ms)}` : undefined} icon={<Activity size={15} />} />
        <StatTile label={`Loss · ${range}`} value={fmtPct(stats?.loss_pct)} sub={stats?.max_loss_pct ? `max ${fmtPct(stats.max_loss_pct)}` : "no loss recorded"} tone={stats && (stats.loss_pct ?? 0) >= t.alert_loss_pct && t.alert_loss_pct > 0 ? "degraded" : undefined} icon={<Percent size={15} />} />
        <StatTile label={`Uptime · ${range}`} value={fmtPct(stats?.availability_pct, stats?.availability_pct === 100 ? 0 : 2)} sub={stats ? `${stats.ok_runs}/${stats.runs} runs reached` : undefined} tone={stats && stats.availability_pct !== null && stats.availability_pct < 99 ? "degraded" : "up"} icon={<Timer size={15} />} />
        <StatTile label={`Jitter · ${range}`} value={stats?.jitter_ms !== null && stats?.jitter_ms !== undefined ? `${fmtNum(stats.jitter_ms)} ms` : "–"} sub={run?.reached ? `now ${fmtNum(run.jitter_avg_ms)} ms` : undefined} icon={<Route size={15} />} />
        <StatTile label={`Reroutes · ${range}`} value={stats?.route_changes ?? 0} sub={stats?.hop_count_min ? `${stats.hop_count_min === stats.hop_count_max ? stats.hop_count_min : `${stats.hop_count_min}–${stats.hop_count_max}`} hops` : undefined} tone={stats?.route_changes ? "degraded" : undefined} icon={<GitBranch size={15} />} />
      </div>

      <div className="card p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Round-trip time to destination</h2>
            <p className="text-xs text-faint">Line: average · Band: best–worst{series.data?.bucket_sec ? ` · aggregated in ${fmtDuration(series.data.bucket_sec)} buckets` : ""} · dashed: route change. Click a point to open the run.</p>
          </div>
          <Legend />
        </div>
        <LatencyChart points={series.data?.points ?? []} rangeSec={series.data?.range_sec ?? 86400} bucketSec={series.data?.bucket_sec ?? null} onPointClick={(rid) => navigate(`/runs/${rid}`)} />
        <div className="mt-2 grid grid-cols-1 gap-3 lg:grid-cols-2">
          <div>
            <h3 className="mb-1 text-xs font-semibold text-muted">Packet loss to destination</h3>
            <LossChart points={series.data?.points ?? []} rangeSec={series.data?.range_sec ?? 86400} bucketSec={series.data?.bucket_sec ?? null} />
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold text-muted">Jitter (average inter-probe variation)</h3>
            <JitterChart points={series.data?.points ?? []} rangeSec={series.data?.range_sec ?? 86400} bucketSec={series.data?.bucket_sec ?? null} />
          </div>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-1 border-b border-border px-2 pt-2">
          {(
            [
              ["path", "Current path"],
              ["history", "Path history"],
              ["summary", `Path summary · ${range}`],
              ["runs", "Runs"],
              ["events", `Events${events.data?.length ? ` (${events.data.length})` : ""}`],
            ] as [Tab, string][]
          ).map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} className={classNames("-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors", tab === k ? "border-accent text-accent" : "border-transparent text-muted hover:text-text")}>
              {label}
            </button>
          ))}
        </div>

        {tab === "path" && (
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-xs text-muted">
              {latestRun ? (
                <span>
                  Run <Link to={`/runs/${latestRun.id}`} className="font-medium text-accent hover:underline">#{latestRun.id}</Link> · {fmtDateTime(latestRun.started_at)} · {latestRun.hop_count} hops · {latestRun.sent ?? t.count} probes/hop{latestRun.src ? ` · from ${latestRun.src}` : ""}
                  {latestRun.status !== "ok" && <span className="ml-2 font-medium text-down">{latestRun.error}</span>}
                  {latestRun.status === "ok" && !latestRun.reached && <span className="ml-2 font-medium text-down">destination did not respond</span>}
                </span>
              ) : (
                <span>Waiting for the first run…</span>
              )}
              {latestRun && (
                <a className="btn btn-ghost btn-sm" href={api.runReportUrl(latestRun.id)} target="_blank" rel="noreferrer"><Download size={13} /> Text report</a>
              )}
            </div>
            {latestRun && <HopTable hops={latestRun.hops} dstIp={latestRun.dst_ip} />}
          </div>
        )}

        {tab === "history" && (
          <div className="p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs text-muted">Each column is one run (oldest left), each row one hop. Hover a cell for details, click a column to open that run.</div>
              <div className="flex flex-wrap items-center gap-3">
                <HeatLegend metric={heatMetric} />
                <Segmented<HeatMetric> value={heatMetric} onChange={setHeatMetric} options={[{ value: "loss", label: "Loss" }, { value: "avg", label: "Latency" }, { value: "jitter", label: "Jitter" }]} />
              </div>
            </div>
            {history.data ? <HopHeatmap history={history.data} metric={heatMetric} rangeSec={series.data?.range_sec ?? 86400} onSelectRun={(rid) => navigate(`/runs/${rid}`)} /> : <div className="py-10 text-center text-sm text-faint">Loading…</div>}
          </div>
        )}

        {tab === "summary" && (
          <div>
            <div className="px-4 py-2.5 text-xs text-muted">Per-hop statistics aggregated over {summary.data?.total_runs ?? 0} runs in the selected range. Expand a hop to see alternate addresses observed at that position (load balancing or reroutes).</div>
            {summary.data ? <PathSummary summary={summary.data} dstIp={run?.dst_ip} /> : <div className="py-10 text-center text-sm text-faint">Loading…</div>}
          </div>
        )}

        {tab === "runs" && (
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
              <Segmented value={runFilter} onChange={setRunFilter} options={[{ value: "", label: "All" }, { value: "ok", label: "Reached" }, { value: "failed", label: "Failed" }, { value: "route_change", label: "Route changes" }]} />
              <Pager page={runPage} pageSize={RUN_PAGE} total={runs.data?.total ?? 0} onChange={setRunPage} />
            </div>
            <RunsTable runs={(runs.data?.items ?? []) as Run[]} now={now} onOpen={(rid) => navigate(`/runs/${rid}`)} />
          </div>
        )}

        {tab === "events" && <EventsList events={events.data ?? []} now={now} showTarget={false} />}
      </div>

      <TargetForm open={editing} initial={t} onClose={() => setEditing(false)} onSubmit={save} submitting={saving} />
      <ConfirmDialog open={deleting} title={`Delete ${t.name}?`} message="This permanently removes the target and all of its recorded runs, hops and events." confirmLabel="Delete target" danger onConfirm={remove} onCancel={() => setDeleting(false)} />
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-3 text-[11px] text-faint">
      <span className="inline-flex items-center gap-1"><span className="inline-block h-[2px] w-4" style={{ background: "var(--chart-avg)" }} /> avg</span>
      <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-4 rounded-sm" style={{ background: "var(--chart-band)", opacity: 0.25 }} /> best–worst</span>
      <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-1 rounded-sm" style={{ background: "var(--chart-jitter)" }} /> route change</span>
    </div>
  );
}

export function Pager({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center gap-2 text-xs text-muted">
      <span className="num">{total ? `${page * pageSize + 1}–${Math.min(total, (page + 1) * pageSize)} of ${total}` : "0 results"}</span>
      <button className="btn btn-sm" disabled={page === 0} onClick={() => onChange(page - 1)}>Prev</button>
      <button className="btn btn-sm" disabled={page + 1 >= pages} onClick={() => onChange(page + 1)}>Next</button>
    </div>
  );
}
