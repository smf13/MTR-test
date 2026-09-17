import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Play, Pause, Pencil, Trash2, Clock, GitBranch, Activity, Percent, Gauge, Timer, Route, RefreshCw, Download, BarChart3, Waypoints, CalendarDays } from "lucide-react";
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
import { PathProfileChart, LatencyHistogram, HourlyHeatmap, RouteTimeline, profileFromHops, profileFromSummary, type HourlyMetric } from "../components/Visuals";
import { StatusStrip } from "../components/StatusStrip";
import { Pager } from "../components/Pager";
import { TypeBadge } from "../components/TypeBadge";
import { TargetForm } from "../components/TargetForm";
import { ConfirmDialog } from "../components/Modal";
import { ErrorBanner } from "../components/EmptyState";
import { TagList, useTagColors } from "../components/Tags";
import { useToast } from "../components/Toast";
import { effectiveStatus, fmtDuration, fmtNum, fmtPct, relTime, fmtDateTime, classNames, hostLabel, isPathProbe, isPacketProbe, latencyLabel } from "../utils";
import { CheckDetails } from "../components/CheckDetails";
import { PROBE_TYPE_LABEL } from "../api";

type Tab = "path" | "history" | "summary" | "runs" | "events";
const RUN_PAGE = 25;

export function TargetDetail() {
  const { id } = useParams();
  const targetId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const now = useNow();
  const { colors: tagColors } = useTagColors();
  const [range, setRange] = useLocalStorage("mtr-tracker.range", "24h");
  const [tab, setTab] = useLocalStorage<Tab>("mtr-tracker.tab", "path");
  const [heatMetric, setHeatMetric] = useState<HeatMetric>("loss");
  const [profileSource, setProfileSource] = useState<"latest" | "range">("latest");
  const [hourlyMetric, setHourlyMetric] = useState<HourlyMetric>("avg");
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
  const routes = usePoll(() => api.routes(targetId, range), pollMs, [targetId, range]);
  const hourly = usePoll(() => api.hourly(targetId, range), Math.max(pollMs, 60000), [targetId, range]);

  useEffect(() => setRunPage(0), [range, runFilter]);

  const refreshAll = useCallback(() => {
    void target.refresh();
    void series.refresh();
    void latest.refresh();
    void history.refresh();
    void summary.refresh();
    void runs.refresh();
    void events.refresh();
    void routes.refresh();
    void hourly.refresh();
  }, [target, series, latest, history, summary, runs, events, routes, hourly]);

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

  const report = (e: unknown) => toast(e instanceof Error ? e.message : String(e), "error");

  const toggle = async () => {
    if (!t) return;
    try {
      await api.updateTarget(t.id, { enabled: !t.enabled });
      toast(t.enabled ? "Monitoring paused" : "Monitoring resumed", "info");
      void target.refresh();
    } catch (e) {
      report(e);
    }
  };

  const runNow = async () => {
    try {
      const r = await api.runNow(targetId);
      toast(r.already_running ? "A probe is already running" : "Probe started", "info");
      window.setTimeout(refreshAll, 2500);
    } catch (e) {
      report(e);
    }
  };

  const remove = async () => {
    try {
      await api.deleteTarget(targetId);
      toast("Target deleted", "info");
      navigate("/");
    } catch (e) {
      setDeleting(false);
      report(e);
    }
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
  const latestRun = latest.data;
  const pathProbe = isPathProbe(t.type, t.options);
  const latencyWord = latencyLabel(t.type, t.options);
  const singleSample = !isPacketProbe(t.type, t.options);
  const latestDetails = (run?.details || {}) as Record<string, unknown>;

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
            <TypeBadge type={t.type} />
            <span className="font-mono break-all">{t.type === "http" ? t.host : hostLabel(t.host)}</span>
            {run?.dst_ip && run.dst_ip !== t.host && t.type !== "http" && <span className="font-mono text-faint">→ {run.dst_ip}</span>}
            {pathProbe && <span className="uppercase">{t.protocol}{t.port ? ` :${t.port}` : ""}</span>}
            {t.type === "tcp" && <span className="font-mono">port {t.port}</span>}
            {t.type === "dns" && <span className="font-mono">{String(t.options.record_type ?? "A")}{t.options.resolver ? ` @${t.options.resolver}` : ""}{t.options.random_prefix ? " · random subdomain (uncached)" : ""}</span>}
            {t.type === "http" && <span className="font-mono">{String(t.options.method ?? "GET")} · expect {String(t.options.expected_status ?? "200-299")}</span>}
            {t.type === "globalping" && <span>{String(t.options.measurement ?? "ping")} via Globalping · from {String(t.options.location ?? "world")}{!pathProbe && (t.options.probes ?? 1) > 1 ? ` · ${t.options.probes} probes` : ""}</span>}
            <span className="inline-flex items-center gap-1"><Clock size={12} /> every {fmtDuration(t.interval_sec)}</span>
            {isPacketProbe(t.type, t.options) && <span>{t.count} {t.type === "globalping" ? "packets per probe" : `probes × ${t.probe_interval}s`}</span>}
            {t.ip_version !== "auto" && <span>IPv{t.ip_version}</span>}
            <TagList tags={t.tags} colors={tagColors} size="xs" />
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
        <StatTile label={`${latencyWord} now`} value={run?.reached ? `${fmtNum(run.avg_ms)} ms` : run ? "unreachable" : "–"} sub={run?.reached ? `best ${fmtNum(run.best_ms)} · worst ${fmtNum(run.worst_ms)}` : run?.error ?? undefined} tone={status === "down" ? "down" : status === "degraded" ? "degraded" : undefined} icon={<Gauge size={15} />} />
        <StatTile label={`Avg · ${range}`} value={stats?.avg_ms !== null && stats?.avg_ms !== undefined ? `${fmtNum(stats.avg_ms)} ms` : "–"} sub={stats?.p95_ms !== null && stats?.p95_ms !== undefined ? `p95 ${fmtNum(stats.p95_ms)} · p99 ${fmtNum(stats.p99_ms)}` : undefined} icon={<Activity size={15} />} />
        <StatTile label={`Loss · ${range}`} value={fmtPct(stats?.loss_pct)} sub={stats?.max_loss_pct ? `max ${fmtPct(stats.max_loss_pct)}` : "no loss recorded"} tone={stats && (stats.loss_pct ?? 0) >= t.alert_loss_pct && t.alert_loss_pct > 0 ? "degraded" : undefined} icon={<Percent size={15} />} />
        <StatTile label={`Uptime · ${range}`} value={fmtPct(stats?.availability_pct, stats?.availability_pct === 100 ? 0 : 2)} sub={stats ? `${stats.ok_runs}/${stats.runs} runs reached` : undefined} tone={stats && stats.availability_pct !== null && stats.availability_pct < 99 ? "degraded" : "up"} icon={<Timer size={15} />} />
        {singleSample ? (
          <StatTile
            label="Last check"
            value={run ? (run.reached ? "Pass" : "Fail") : "–"}
            sub={t.type === "http" ? (latestDetails.status !== undefined ? `HTTP ${latestDetails.status}${latestDetails.tls_expires_in_days !== undefined && latestDetails.tls_expires_in_days !== null ? ` · TLS ${latestDetails.tls_expires_in_days}d` : ""}` : run?.error ?? undefined) : t.type === "dns" ? (Array.isArray(latestDetails.answers) ? `${(latestDetails.answers as unknown[]).length} answer${(latestDetails.answers as unknown[]).length === 1 ? "" : "s"}` : run?.error ?? undefined) : run?.error ?? (t.port ? `port ${t.port}` : undefined)}
            tone={run ? (run.reached ? "up" : "down") : undefined}
            icon={<Route size={15} />}
          />
        ) : (
          <StatTile label={`Jitter · ${range}`} value={stats?.jitter_ms !== null && stats?.jitter_ms !== undefined ? `${fmtNum(stats.jitter_ms)} ms` : "–"} sub={run?.reached ? `now ${fmtNum(run.jitter_avg_ms)} ms` : undefined} icon={<Route size={15} />} />
        )}
        {pathProbe ? (
          <StatTile label={`Reroutes · ${range}`} value={stats?.route_changes ?? 0} sub={stats?.hop_count_min ? `${stats.hop_count_min === stats.hop_count_max ? stats.hop_count_min : `${stats.hop_count_min}–${stats.hop_count_max}`} hops` : undefined} tone={stats?.route_changes ? "degraded" : undefined} icon={<GitBranch size={15} />} />
        ) : (
          <StatTile label={`Failed checks · ${range}`} value={stats?.failed_runs ?? 0} sub={stats ? `of ${stats.runs} runs` : undefined} tone={stats?.failed_runs ? "down" : "up"} icon={<GitBranch size={15} />} />
        )}
      </div>

      <div className="card px-4 py-3">
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="font-semibold">Status · last 24 hours</span>
          <span className="text-faint">{fmtDuration(t.timeline.bucket_sec)} per cell · hover for details</span>
        </div>
        <StatusStrip buckets={t.timeline.buckets} bucketSec={t.timeline.bucket_sec} since={t.timeline.since} height={12} />
      </div>

      <div className="card p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">{pathProbe ? "Round-trip time to destination" : `${latencyWord} time per run`}</h2>
            <p className="text-xs text-faint">Line: average · Band: best–worst{series.data?.bucket_sec ? ` · aggregated in ${fmtDuration(series.data.bucket_sec)} buckets` : ""} · dashed: route change. Click a point to open the run.</p>
          </div>
          <Legend />
        </div>
        <LatencyChart points={series.data?.points ?? []} rangeSec={series.data?.range_sec ?? 86400} bucketSec={series.data?.bucket_sec ?? null} onPointClick={(rid) => navigate(`/runs/${rid}`)} />
        {pathProbe && (
          <div className="mt-2 pl-11 pr-3">
            <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-muted"><Waypoints size={13} /> Route in use{routes.data ? ` · ${routes.data.routes.length} distinct path${routes.data.routes.length === 1 ? "" : "s"}` : ""}</div>
            {routes.data ? <RouteTimeline routes={routes.data} onOpenRun={(rid) => navigate(`/runs/${rid}`)} /> : <div className="h-4" />}
          </div>
        )}
        <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
          <div>
            <h3 className="mb-1 text-xs font-semibold text-muted">{isPacketProbe(t.type, t.options) ? "Packet loss to destination" : "Failed checks (bar = check failed)"}</h3>
            <LossChart points={series.data?.points ?? []} rangeSec={series.data?.range_sec ?? 86400} bucketSec={series.data?.bucket_sec ?? null} />
          </div>
          {singleSample ? null : (
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted">Jitter (average inter-probe variation)</h3>
              <JitterChart points={series.data?.points ?? []} rangeSec={series.data?.range_sec ?? 86400} bucketSec={series.data?.bucket_sec ?? null} />
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {pathProbe ? (
          <div className="card p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-semibold"><Route size={15} /> Path profile</h2>
                <p className="text-xs text-faint">Where latency is added along the path. Line: avg per hop · band: best–worst · bars: loss per hop.</p>
              </div>
              <Segmented value={profileSource} onChange={setProfileSource} options={[{ value: "latest", label: "Latest run" }, { value: "range", label: `Avg · ${range}` }]} />
            </div>
            <PathProfileChart rows={profileSource === "latest" ? (latestRun ? profileFromHops(latestRun.hops) : []) : summary.data ? profileFromSummary(summary.data) : []} />
          </div>
        ) : (
          <div className="card p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-semibold"><Route size={15} /> Latest check · {PROBE_TYPE_LABEL[t.type]}</h2>
                <p className="text-xs text-faint">{latestRun ? <>Run <Link to={`/runs/${latestRun.id}`} className="text-accent hover:underline">#{latestRun.id}</Link> · {fmtDateTime(latestRun.started_at)}</> : "Waiting for the first run…"}</p>
              </div>
            </div>
            {latestRun ? <CheckDetails run={latestRun} type={t.type} /> : null}
          </div>
        )}
        <div className="card p-4">
          <div className="mb-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold"><BarChart3 size={15} /> Latency distribution · {range}</h2>
            <p className="text-xs text-faint">How often each round-trip time occurs{series.data?.bucket_sec ? ` (from ${fmtDuration(series.data.bucket_sec)} averages)` : ""}. A long right tail means occasional slow runs; two humps mean two paths or states.</p>
          </div>
          <LatencyHistogram points={series.data?.points ?? []} />
        </div>
      </div>

      <div className="card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold"><CalendarDays size={15} /> Hour by day · {range}</h2>
            <p className="text-xs text-faint">One cell per hour in your local time. Recurring dark columns mean congestion at the same time each day.</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <HeatLegend metric={hourlyMetric === "loss" ? "loss" : "avg"} />
            <Segmented<HourlyMetric> value={hourlyMetric} onChange={setHourlyMetric} options={[{ value: "avg", label: "Latency" }, { value: "loss", label: "Loss" }, { value: "jitter", label: "Jitter" }]} />
          </div>
        </div>
        {hourly.data ? <HourlyHeatmap hours={hourly.data.hours} metric={hourlyMetric} /> : <div className="py-10 text-center text-sm text-faint">Loading…</div>}
      </div>

      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-1 border-b border-border px-2 pt-2">
          {(
            (pathProbe
              ? [
                  ["path", "Current path"],
                  ["history", "Path history"],
                  ["summary", `Path summary · ${range}`],
                  ["runs", "Runs"],
                  ["events", `Events${events.data?.length ? ` (${events.data.length})` : ""}`],
                ]
              : [
                  ["runs", "Runs"],
                  ["events", `Events${events.data?.length ? ` (${events.data.length})` : ""}`],
                ]) as [Tab, string][]
          ).map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} className={classNames("-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors", tab === k ? "border-accent text-accent" : "border-transparent text-muted hover:text-text")}>
              {label}
            </button>
          ))}
        </div>

        {!pathProbe && !["runs", "events"].includes(tab) && <RunsFallback onPick={() => setTab("runs")} />}
        {pathProbe && tab === "path" && (
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

        {pathProbe && tab === "history" && (
          <div className="p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs text-muted">Each column is one run (oldest left), each row one hop. Hover a cell for details, click a column to open that run.</div>
              <div className="flex flex-wrap items-center gap-3">
                <HeatLegend metric={heatMetric} />
                <Segmented<HeatMetric> value={heatMetric} onChange={setHeatMetric} options={[{ value: "loss", label: "Loss" }, { value: "avg", label: "Latency" }, { value: "jitter", label: "Jitter" }]} />
              </div>
            </div>
            {history.data ? (
              <HopHeatmap history={history.data} metric={heatMetric} rangeSec={series.data?.range_sec ?? 86400} onSelectRun={(rid) => navigate(`/runs/${rid}`)} />
            ) : history.error ? (
              <ErrorBanner message={`Could not load path history: ${history.error}`} />
            ) : (
              <div className="py-10 text-center text-sm text-faint">Loading…</div>
            )}
          </div>
        )}

        {pathProbe && tab === "summary" && (
          <div>
            <div className="px-4 py-2.5 text-xs text-muted">Per-hop statistics aggregated over {summary.data?.total_runs ?? 0} runs in the selected range. Expand a hop to see alternate addresses observed at that position (load balancing or reroutes).</div>
            {summary.data ? <PathSummary summary={summary.data} dstIp={run?.dst_ip} /> : <div className="py-10 text-center text-sm text-faint">Loading…</div>}
          </div>
        )}

        {tab === "runs" && (
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
              <Segmented value={runFilter} onChange={setRunFilter} options={[{ value: "", label: "All" }, { value: "ok", label: isPacketProbe(t.type, t.options) ? "Reached" : "Passed" }, { value: "failed", label: "Failed" }, ...(pathProbe ? [{ value: "route_change" as const, label: "Route changes" }] : [])]} />
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

function RunsFallback({ onPick }: { onPick: () => void }) {
  useEffect(() => onPick(), [onPick]);
  return null;
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
