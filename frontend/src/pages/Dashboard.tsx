import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Plus, Search, Play, Pause, Pencil, Trash2, Clock, GitBranch, Activity, ShieldCheck, ShieldAlert, ShieldOff, RefreshCw, ChevronDown, ChevronUp, LineChart } from "lucide-react";
import { api, PROBE_TYPE_LABEL, type Target, type TargetInput } from "../api";
import { usePoll, useNow, useLocalStorage } from "../hooks";
import { StatusBadge } from "../components/StatusBadge";
import { StatTile } from "../components/StatTile";
import { Sparkline } from "../components/Sparkline";
import { TargetForm } from "../components/TargetForm";
import { ConfirmDialog } from "../components/Modal";
import { EmptyState, ErrorBanner } from "../components/EmptyState";
import { Segmented } from "../components/RangePicker";
import { OverviewChart, StatusStrip } from "../components/Visuals";
import { TagList, useTagColors } from "../components/Tags";
import { useToast } from "../components/Toast";
import { effectiveStatus, fmtDuration, fmtNum, fmtPct, relTime, classNames, lossColor, statusColor, hostLabel, isPathProbe } from "../utils";

type SortKey = "name" | "status" | "latency" | "loss" | "hops";
type View = "cards" | "table";

const STATUS_ORDER: Record<string, number> = { down: 0, degraded: 1, pending: 2, up: 3, paused: 4 };

export function Dashboard() {
  const targets = usePoll(() => api.targets(), 10000);
  const overview = usePoll(() => api.overview("24h"), 60000);
  const [showOverview, setShowOverview] = useLocalStorage("mtr-tracker.overview", true);
  const now = useNow();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useLocalStorage<SortKey>("mtr-tracker.sort", "status");
  const [view, setView] = useLocalStorage<View>("mtr-tracker.view", "cards");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Target | null>(null);
  const [deleting, setDeleting] = useState<Target | null>(null);
  const [saving, setSaving] = useState(false);
  const [prefill, setPrefill] = useState<Partial<TargetInput> | null>(null);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const host = (location.state as { prefillHost?: string } | null)?.prefillHost;
    if (host) {
      setPrefill({ host, name: host });
      setEditing(null);
      setFormOpen(true);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate]);

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    let items = targets.data ?? [];
    if (q) items = items.filter((t) => [t.name, t.host, t.description, ...t.tags].some((s) => s.toLowerCase().includes(q)));
    const sorted = [...items];
    sorted.sort((a, b) => {
      switch (sort) {
        case "status":
          return STATUS_ORDER[effectiveStatus(a)] - STATUS_ORDER[effectiveStatus(b)] || a.name.localeCompare(b.name);
        case "latency":
          return (b.latest_run?.avg_ms ?? -1) - (a.latest_run?.avg_ms ?? -1);
        case "loss":
          return (b.latest_run?.loss_pct ?? -1) - (a.latest_run?.loss_pct ?? -1);
        case "hops":
          return (b.latest_run?.hop_count ?? -1) - (a.latest_run?.hop_count ?? -1) || a.name.localeCompare(b.name);
        default:
          return a.name.localeCompare(b.name);
      }
    });
    return sorted;
  }, [targets.data, query, sort]);

  const counts = useMemo(() => {
    const c = { up: 0, degraded: 0, down: 0, paused: 0, pending: 0 };
    (targets.data ?? []).forEach((t) => {
      c[effectiveStatus(t)] += 1;
    });
    return c;
  }, [targets.data]);

  const submit = async (values: TargetInput) => {
    setSaving(true);
    try {
      if (editing) {
        await api.updateTarget(editing.id, values);
        toast(`Updated ${values.name}`, "success");
      } else {
        await api.createTarget(values);
        toast(`Added ${values.name}. First run starts now.`, "success");
      }
      setFormOpen(false);
      setEditing(null);
      await targets.refresh();
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (t: Target) => {
    try {
      await api.updateTarget(t.id, { enabled: !t.enabled });
      toast(t.enabled ? `Paused ${t.name}` : `Resumed ${t.name}`, "info");
      await targets.refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const runNow = async (t: Target) => {
    try {
      const r = await api.runNow(t.id);
      toast(r.already_running ? `${t.name} is already being probed` : `Probing ${t.name} now`, "info");
      window.setTimeout(() => void targets.refresh(), 1500);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const remove = async () => {
    if (!deleting) return;
    try {
      await api.deleteTarget(deleting.id);
      toast(`Deleted ${deleting.name}`, "info");
      setDeleting(null);
      await targets.refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const total = targets.data?.length ?? 0;
  const activeAvg = useMemo(() => {
    const vals = (targets.data ?? []).map((t) => t.latest_run?.avg_ms).filter((v): v is number => typeof v === "number");
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }, [targets.data]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted">Continuous MTR monitoring · {total} target{total === 1 ? "" : "s"}</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn" onClick={() => targets.refresh()} title="Refresh">
            <RefreshCw size={15} className={classNames(targets.loading && "animate-spin")} />
          </button>
          <button
            className="btn btn-primary"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            <Plus size={16} /> Add target
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Up" value={counts.up} tone="up" icon={<ShieldCheck size={16} />} />
        <StatTile label="Degraded" value={counts.degraded} tone={counts.degraded ? "degraded" : undefined} icon={<ShieldAlert size={16} />} />
        <StatTile label="Down" value={counts.down} tone={counts.down ? "down" : undefined} icon={<ShieldOff size={16} />} />
        <StatTile label="Paused" value={counts.paused} tone="muted" icon={<Pause size={16} />} />
        <StatTile label="Mean latency" value={activeAvg !== null ? `${fmtNum(activeAvg)} ms` : "–"} sub="latest run, all targets" icon={<Activity size={16} />} />
        <StatTile label="Route changes" value={(targets.data ?? []).reduce((a, t) => a + t.stats_24h.route_changes, 0)} sub="last 24 hours" icon={<GitBranch size={16} />} />
      </div>

      {targets.error && <ErrorBanner message={`Could not load targets: ${targets.error}`} />}

      {overview.data && overview.data.targets.length > 0 && (
        <div className="card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold"><LineChart size={15} /> Latency across targets · 24h</h2>
              <p className="text-xs text-faint">Average round-trip time to each destination in {fmtDuration(overview.data.bucket_sec)} buckets. Click a name to hide or show it.</p>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => setShowOverview(!showOverview)}>{showOverview ? <><ChevronUp size={14} /> Collapse</> : <><ChevronDown size={14} /> Expand</>}</button>
          </div>
          {showOverview && <div className="mt-2"><OverviewChart data={overview.data} /></div>}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1 sm:max-w-xs">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
          <input className="input pl-9" placeholder="Filter by name, host or tag" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Segmented<SortKey>
          value={sort}
          onChange={setSort}
          options={[
            { value: "status", label: "By status" },
            { value: "name", label: "Name" },
            { value: "latency", label: "Latency" },
            { value: "loss", label: "Loss" },
            { value: "hops", label: "Hops" },
          ]}
        />
        <Segmented<View>
          value={view}
          onChange={setView}
          options={[
            { value: "cards", label: "Cards" },
            { value: "table", label: "Table" },
          ]}
        />
      </div>

      {!targets.loading && total === 0 && (
        <div className="card">
          <EmptyState
            icon={<Activity size={36} />}
            title="No targets yet"
            body="Add a host to start running MTR on a schedule. MTR Tracker records every hop of every run so you can see exactly where latency and loss appear."
            action={
              <button className="btn btn-primary" onClick={() => setFormOpen(true)}>
                <Plus size={16} /> Add your first target
              </button>
            }
          />
        </div>
      )}

      {view === "cards" ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
          {list.map((t) => (
            <TargetCard key={t.id} t={t} now={now} onEdit={() => { setEditing(t); setFormOpen(true); }} onDelete={() => setDeleting(t)} onToggle={() => toggle(t)} onRun={() => runNow(t)} />
          ))}
        </div>
      ) : (
        <div className="card overflow-hidden">
          <TargetTable list={list} now={now} onEdit={(t) => { setEditing(t); setFormOpen(true); }} onDelete={setDeleting} onToggle={toggle} onRun={runNow} />
        </div>
      )}

      <TargetForm open={formOpen} initial={editing} prefill={prefill} onClose={() => { setFormOpen(false); setEditing(null); setPrefill(null); }} onSubmit={submit} submitting={saving} />
      <ConfirmDialog
        open={!!deleting}
        title={`Delete ${deleting?.name ?? ""}?`}
        message={<>This permanently removes the target and all of its recorded runs, hops and events. This cannot be undone.</>}
        confirmLabel="Delete target"
        danger
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

function TargetCard({ t, now, onEdit, onDelete, onToggle, onRun }: { t: Target; now: number; onEdit: () => void; onDelete: () => void; onToggle: () => void; onRun: () => void }) {
  const status = effectiveStatus(t);
  const run = t.latest_run;
  const loss = run?.loss_pct ?? null;
  const { colors: tagColors } = useTagColors();
  return (
    <div className="card fade-in group relative overflow-hidden p-4" style={{ borderLeft: `3px solid ${statusColor(status)}` }}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/targets/${t.id}`} className="block truncate text-base font-semibold hover:text-accent">
            {t.name}
          </Link>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
            <TypeBadge type={t.type} />
            <span className="font-mono truncate max-w-[260px]" title={t.host}>{hostLabel(t.host)}</span>
            {run?.dst_ip && run.dst_ip !== t.host && t.type !== "http" && <span className="font-mono text-faint">{run.dst_ip}</span>}
            {t.type === "mtr" && <><span className="text-faint">·</span><span className="uppercase">{t.protocol}{t.port ? `/${t.port}` : ""}</span></>}
            {t.type === "tcp" && <span className="font-mono">:{t.port}</span>}
            {t.type === "dns" && <span className="font-mono">{String(t.options.record_type ?? "A")}</span>}
          </div>
        </div>
        <StatusBadge status={status} running={t.running} />
      </div>

      <div className="mt-3 grid grid-cols-4 gap-2">
        <Metric label={t.type === "http" ? "Response" : t.type === "tcp" ? "Connect" : t.type === "dns" ? "Lookup" : "Latency"} value={run?.reached ? fmtNum(run.avg_ms) : "–"} unit="ms" />
        {isPathProbe(t.type) || t.type === "ping" ? (
          <Metric label="Loss" value={fmtNum(loss)} unit="%" color={loss && loss > 0 ? lossColor(loss) : undefined} />
        ) : (
          <Metric label="Check" value={run ? (run.reached ? "pass" : "fail") : "–"} color={run ? (run.reached ? "var(--up)" : "var(--down)") : undefined} />
        )}
        <ThirdMetric t={t} />
        <Metric label="Avail 24h" value={t.stats_24h.availability_pct !== null ? fmtNum(t.stats_24h.availability_pct, t.stats_24h.availability_pct === 100 ? 0 : 1) : "–"} unit="%" />
      </div>

      <div className="mt-3">
        <StatusStrip buckets={t.timeline.buckets} bucketSec={t.timeline.bucket_sec} since={t.timeline.since} />
      </div>

      <div className="mt-2 flex items-end justify-between gap-3">
        <Link to={`/targets/${t.id}`} className="block min-w-0 flex-1" title="Latency of the last runs">
          <div className="w-full">
            <Sparkline points={t.sparkline} width={300} height={40} color={statusColor(status === "paused" || status === "pending" ? "up" : status)} />
          </div>
        </Link>
      </div>

      <div className="mt-3 flex items-center justify-between border-t border-border pt-2.5 text-[11px] text-faint">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1">
            <Clock size={11} /> every {fmtDuration(t.interval_sec)}
          </span>
          <span>{run ? `last ${relTime(run.started_at, now)}` : "not yet run"}</span>
          <TagList tags={t.tags} colors={tagColors} max={3} size="xs" />
        </div>
        <div className="flex items-center gap-0.5 opacity-70 transition-opacity group-hover:opacity-100">
          <IconBtn title="Run now" onClick={onRun}><Play size={13} /></IconBtn>
          <IconBtn title={t.enabled ? "Pause" : "Resume"} onClick={onToggle}>{t.enabled ? <Pause size={13} /> : <Play size={13} />}</IconBtn>
          <IconBtn title="Edit" onClick={onEdit}><Pencil size={13} /></IconBtn>
          <IconBtn title="Delete" onClick={onDelete} danger><Trash2 size={13} /></IconBtn>
        </div>
      </div>
    </div>
  );
}

export function TypeBadge({ type }: { type: Target["type"] }) {
  return (
    <span className="rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
      {PROBE_TYPE_LABEL[type] ?? type}
    </span>
  );
}

function ThirdMetric({ t }: { t: Target }) {
  const run = t.latest_run;
  const d = (run?.details || {}) as Record<string, unknown>;
  switch (t.type) {
    case "http":
      return <Metric label="HTTP" value={d.status !== undefined ? String(d.status) : "–"} color={d.status !== undefined ? (d.status_ok ? "var(--up)" : "var(--down)") : undefined} />;
    case "tcp":
      return <Metric label="Port" value={t.port ? String(t.port) : "–"} />;
    case "dns":
      return <Metric label="Answers" value={Array.isArray(d.answers) ? String((d.answers as unknown[]).length) : "–"} />;
    case "ping":
      return <Metric label="Jitter" value={run?.reached ? fmtNum(run.jitter_avg_ms) : "–"} unit="ms" />;
    default:
      return <Metric label="Hops" value={run?.hop_count ? String(run.hop_count) : "–"} />;
  }
}

function Metric({ label, value, unit, color }: { label: string; value: string; unit?: string; color?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-faint">{label}</div>
      <div className="num truncate text-lg font-semibold leading-tight" style={{ color }}>
        {value}
        {unit && value !== "–" && <span className="ml-0.5 text-xs font-normal text-faint">{unit}</span>}
      </div>
    </div>
  );
}

export function IconBtn({ title, onClick, children, danger }: { title: string; onClick: () => void; children: React.ReactNode; danger?: boolean }) {
  return (
    <button className={classNames("rounded-md p-1.5 transition-colors hover:bg-surface-2", danger ? "text-faint hover:text-down" : "text-faint hover:text-text")} title={title} aria-label={title} onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClick(); }}>
      {children}
    </button>
  );
}

function TargetTable({ list, now, onEdit, onDelete, onToggle, onRun }: { list: Target[]; now: number; onEdit: (t: Target) => void; onDelete: (t: Target) => void; onToggle: (t: Target) => void; onRun: (t: Target) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="table num">
        <thead>
          <tr>
            <th>Target</th>
            <th>Status</th>
            <th className="text-right">Latency</th>
            <th className="text-right">Best</th>
            <th className="text-right">Worst</th>
            <th className="text-right">Loss</th>
            <th className="text-right">Hops / HTTP</th>
            <th className="text-right">Avail 24h</th>
            <th className="text-right">Avg 24h</th>
            <th>Trend</th>
            <th className="w-40">Status 24h</th>
            <th>Interval</th>
            <th>Last run</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {list.map((t) => {
            const status = effectiveStatus(t);
            const run = t.latest_run;
            return (
              <tr key={t.id}>
                <td className="font-sans">
                  <Link to={`/targets/${t.id}`} className="font-semibold hover:text-accent">
                    {t.name}
                  </Link>
                  <div className="flex items-center gap-1.5 font-mono text-[11px] text-faint"><TypeBadge type={t.type} /><span className="truncate max-w-[220px]" title={t.host}>{hostLabel(t.host)}</span></div>
                </td>
                <td><StatusBadge status={status} running={t.running} /></td>
                <td className="text-right font-semibold">{run?.reached ? fmtNum(run.avg_ms) : "–"}</td>
                <td className="text-right text-muted">{run?.reached ? fmtNum(run.best_ms) : "–"}</td>
                <td className="text-right text-muted">{run?.reached ? fmtNum(run.worst_ms) : "–"}</td>
                <td className="text-right font-semibold" style={{ color: (run?.loss_pct ?? 0) > 0 ? lossColor(run?.loss_pct) : undefined }}>{fmtPct(run?.loss_pct)}</td>
                <td className="text-right">{t.type === "mtr" ? run?.hop_count || "–" : t.type === "http" ? String((run?.details as Record<string, unknown> | null)?.status ?? "–") : "–"}</td>
                <td className="text-right">{fmtPct(t.stats_24h.availability_pct)}</td>
                <td className="text-right text-muted">{fmtNum(t.stats_24h.avg_ms)}</td>
                <td><Sparkline points={t.sparkline} width={120} height={26} color={statusColor(status === "paused" || status === "pending" ? "up" : status)} /></td>
                <td><StatusStrip buckets={t.timeline.buckets} bucketSec={t.timeline.bucket_sec} since={t.timeline.since} height={10} className="w-36" /></td>
                <td className="text-muted">{fmtDuration(t.interval_sec)}</td>
                <td className="text-muted">{run ? relTime(run.started_at, now) : "–"}</td>
                <td>
                  <div className="flex items-center">
                    <IconBtn title="Run now" onClick={() => onRun(t)}><Play size={13} /></IconBtn>
                    <IconBtn title={t.enabled ? "Pause" : "Resume"} onClick={() => onToggle(t)}>{t.enabled ? <Pause size={13} /> : <Play size={13} />}</IconBtn>
                    <IconBtn title="Edit" onClick={() => onEdit(t)}><Pencil size={13} /></IconBtn>
                    <IconBtn title="Delete" onClick={() => onDelete(t)} danger><Trash2 size={13} /></IconBtn>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
