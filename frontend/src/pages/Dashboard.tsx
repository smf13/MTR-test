import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Plus, Search, Play, Clock, GitBranch, Activity, ShieldCheck, RefreshCw, ChevronDown, ChevronUp, LineChart } from "lucide-react";
import { api, cloneInput, type Target, type TargetInput } from "../api";
import { usePoll, useNow, useLocalStorage } from "../hooks";
import { MutedBadge, TargetActions } from "../components/TargetActions";
import { HelpTip } from "../components/Popover";
import { StatusBadge } from "../components/StatusBadge";
import { StatTile } from "../components/StatTile";
import { Sparkline } from "../components/Sparkline";
import { TargetForm } from "../components/TargetForm";
import { ConfirmDialog } from "../components/Modal";
import { EmptyState, ErrorBanner } from "../components/EmptyState";
import { Segmented } from "../components/RangePicker";
import { StatusStrip } from "../components/StatusStrip";
import { TypeBadge } from "../components/TypeBadge";
import { TagList, useTagColors } from "../components/Tags";
import { useToast } from "../components/Toast";

// Recharts only loads when the overview chart is actually shown.
const OverviewChart = lazy(() => import("../components/Visuals").then((m) => ({ default: m.OverviewChart })));
import { effectiveStatus, fmtDuration, fmtNum, fmtPct, relTime, classNames, lossColor, statusColor, hostLabel, isPathProbe, isPacketProbe, latencyLabel, percentile } from "../utils";

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
  // Set while cloning, so the form says which target the copy comes from.
  const [formTitle, setFormTitle] = useState<string | undefined>(undefined);
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

  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
    setPrefill(null);
    setFormTitle(undefined);
  };

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
      closeForm();
      await targets.refresh();
    } finally {
      setSaving(false);
    }
  };

  /** Open the add form with every setting of `t` and a "(copy)" name; saving creates a new target. */
  const clone = (t: Target) => {
    setEditing(null);
    setPrefill(cloneInput(t));
    setFormTitle(`Clone ${t.name}`);
    setFormOpen(true);
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

  const toggleNotify = async (t: Target) => {
    try {
      await api.updateTarget(t.id, { notify: !t.notify });
      toast(t.notify ? `Notifications muted for ${t.name}` : `Notifications unmuted for ${t.name}`, "info");
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
  const latency = useMemo(() => {
    const vals = (targets.data ?? []).map((t) => t.latest_run?.avg_ms).filter((v): v is number => typeof v === "number");
    return { mean: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null, median: percentile(vals, 0.5) };
  }, [targets.data]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted">Live network monitoring · {total} target{total === 1 ? "" : "s"}</p>
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

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-[2fr_1fr_1fr_1fr]">
        <div className="card col-span-2 px-4 py-3 md:col-span-3 xl:col-span-1">
          <div className="flex items-center gap-2 text-xs font-medium text-muted"><ShieldCheck size={15} /> Target health</div>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
            {(["up", "degraded", "down", "paused", ...(counts.pending ? ["pending"] : [])] as (keyof typeof counts)[]).map((key) => (
              <div key={key}>
                <div className="num text-2xl font-semibold leading-tight" style={{ color: counts[key] ? statusColor(key) : "var(--text-muted)" }}>{counts[key]}</div>
                <div className="mt-1 text-xs capitalize text-muted">{key}</div>
              </div>
            ))}
          </div>
        </div>
        <StatTile label="Mean latency" value={latency.mean !== null ? `${fmtNum(latency.mean)} ms` : "–"} sub="latest run, all targets" icon={<Activity size={16} />} />
        <StatTile label="Median latency" value={latency.median !== null ? `${fmtNum(latency.median)} ms` : "–"} sub="latest run, all targets" icon={<Activity size={16} />} />
        <StatTile label="Route changes" value={(targets.data ?? []).reduce((a, t) => a + t.stats_24h.route_changes, 0)} sub="last 24 hours" icon={<GitBranch size={16} />} />
      </div>

      {targets.error && <ErrorBanner message={`Could not load targets: ${targets.error}`} />}

      {overview.data && overview.data.targets.length > 0 && (
        <div className="card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="chart-heading"><LineChart size={15} /> Latency across targets · 24h</h2>
              <p className="chart-caption">Destination latency · {fmtDuration(overview.data.bucket_sec)} averages</p>
            </div>
            <div className="flex items-center gap-1"><HelpTip label="latency across targets">Compare destinations over the last 24 hours. Each point averages runs in a {fmtDuration(overview.data.bucket_sec)} bucket. Select a legend label to hide or show a target.</HelpTip><button className="btn btn-ghost btn-sm" aria-expanded={showOverview} onClick={() => setShowOverview(!showOverview)}>{showOverview ? <><ChevronUp size={14} /> Collapse</> : <><ChevronDown size={14} /> Expand</>}</button></div>
          </div>
          {showOverview && (
            <div className="mt-2">
              <Suspense fallback={<div style={{ height: 240 }} />}>
                <OverviewChart data={overview.data} />
              </Suspense>
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1 sm:max-w-xs">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
          <input className="input pl-9" aria-label="Filter targets" placeholder="Filter by name, host or tag" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          Sort
          <select className="input w-auto" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            <option value="status">Status</option><option value="name">Name</option><option value="latency">Latency</option><option value="loss">Loss</option><option value="hops">Hops</option>
          </select>
        </label>
        <div className="sm:ml-auto">
        <Segmented<View>
          value={view}
          onChange={setView}
          options={[
            { value: "cards", label: "Cards" },
            { value: "table", label: "Table" },
          ]}
        />
        </div>
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

      {total > 0 && !list.length && <div className="card"><EmptyState title="No matching targets" body="Try a different name, host, or tag." action={<button className="btn" onClick={() => setQuery("")}>Clear filter</button>} /></div>}

      {view === "cards" ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
          {list.map((t) => (
            <TargetCard key={t.id} t={t} now={now} onEdit={() => { setEditing(t); setFormOpen(true); }} onClone={() => clone(t)} onDelete={() => setDeleting(t)} onToggle={() => toggle(t)} onToggleNotify={() => toggleNotify(t)} onRun={() => runNow(t)} />
          ))}
        </div>
      ) : (
        <div className="card overflow-hidden">
          <TargetTable list={list} now={now} onEdit={(t) => { setEditing(t); setFormOpen(true); }} onClone={clone} onDelete={setDeleting} onToggle={toggle} onToggleNotify={toggleNotify} onRun={runNow} />
        </div>
      )}

      <TargetForm open={formOpen} initial={editing} prefill={prefill} title={formTitle} submitLabel={formTitle ? "Create clone" : undefined} onClose={closeForm} onSubmit={submit} submitting={saving} />
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

function TargetCard({ t, now, onEdit, onClone, onDelete, onToggle, onToggleNotify, onRun }: { t: Target; now: number; onEdit: () => void; onClone: () => void; onDelete: () => void; onToggle: () => void; onToggleNotify: () => void; onRun: () => void }) {
  const status = effectiveStatus(t);
  const run = t.latest_run;
  const loss = run?.loss_pct ?? null;
  const { colors: tagColors } = useTagColors();
  return (
    <article className="card fade-in min-w-0 p-4" aria-label={t.name}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/targets/${t.id}`} className="block truncate text-base font-semibold hover:text-accent">{t.name}</Link>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted"><TypeBadge type={t.type} /><span className="truncate font-mono" title={t.host}>{hostLabel(t.host)}</span></div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {!t.notify && <MutedBadge />}
          <StatusBadge status={status} running={t.running} />
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 items-center gap-4">
        <div>
          <div className="text-xs font-medium text-muted">{latencyLabel(t.type, t.options)}</div>
          <div className="num mt-1 text-3xl font-semibold tracking-tight">{run?.reached ? fmtNum(run.avg_ms) : "–"}{run?.reached && <span className="ml-1 text-sm font-normal text-muted">ms</span>}</div>
        </div>
        <Link to={`/targets/${t.id}`} className="block min-w-0" aria-label={`View latency for ${t.name}`}>
          <Sparkline points={t.sparkline} width={300} height={48} fluid />
        </Link>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3">
        {isPacketProbe(t.type, t.options) ? (
          <Metric label="Loss" value={fmtNum(loss)} unit="%" color={loss && loss > 0 ? lossColor(loss) : undefined} />
        ) : (
          <Metric label="Check" value={run ? (run.reached ? "Pass" : "Fail") : "–"} color={run && !run.reached ? "var(--down)" : undefined} />
        )}
        <Metric label="Avail. · 24h" value={t.stats_24h.availability_pct !== null ? fmtNum(t.stats_24h.availability_pct, t.stats_24h.availability_pct === 100 ? 0 : 1) : "–"} unit="%" />
        <ThirdMetric t={t} />
      </div>
      <div className="mt-4">
        <div className="mb-1.5 flex justify-between text-xs text-muted"><span>Status · last 24h</span><span>Now</span></div>
        <StatusStrip buckets={t.timeline.buckets} bucketSec={t.timeline.bucket_sec} since={t.timeline.since} height={10} />
      </div>
      <div className="mt-4 border-t border-border pt-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <span className="inline-flex items-center gap-1"><Clock size={12} /> Every {fmtDuration(t.interval_sec)}</span>
          <span>{run ? `Last ${relTime(run.started_at, now)}` : "Waiting for first run"}</span>
          {t.type === "mtr" && <span className="uppercase">{t.protocol}{t.port ? `/${t.port}` : ""}</span>}
          {run?.dst_ip && run.dst_ip !== t.host && t.type !== "http" && <span className="break-all font-mono" title="Resolved destination">{run.dst_ip}</span>}
          {t.type === "tcp" && <span>Port {t.port}</span>}
          {t.type === "dns" && <span>{String(t.options.record_type ?? "A")}{t.options.random_prefix ? " · uncached" : ""}</span>}
          {t.type === "globalping" && <span>{String(t.options.measurement ?? "ping")} · {String(t.options.location ?? "world")}</span>}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="min-w-0"><TagList tags={t.tags} colors={tagColors} max={2} size="xs" /></div>
          <div className="flex shrink-0 items-center gap-1">
            <button className="btn btn-ghost btn-sm" disabled={t.running} onClick={onRun}><Play size={14} />{t.running ? "Probing…" : "Run now"}</button>
            <TargetActions name={t.name} enabled={t.enabled} notify={t.notify} onEdit={onEdit} onClone={onClone} onDelete={onDelete} onToggle={onToggle} onToggleNotify={onToggleNotify} />
          </div>
        </div>
      </div>
    </article>
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
    case "globalping": {
      if (isPathProbe(t.type, t.options)) return <Metric label="Hops" value={run?.hop_count ? String(run.hop_count) : "–"} />;
      const measurement = String(t.options.measurement ?? "ping");
      if (measurement === "dns") return <Metric label="Answers" value={Array.isArray(d.answers) ? String((d.answers as unknown[]).length) : "–"} />;
      if (measurement === "http") {
        const has = d.status !== undefined && d.status !== null;
        return <Metric label="HTTP" value={has ? String(d.status) : "–"} color={has ? (run?.reached ? "var(--up)" : "var(--down)") : undefined} />;
      }
      const first = (Array.isArray(d.probes) ? d.probes[0] : undefined) as { city?: string | null; country?: string | null } | undefined;
      return <Metric label="From" value={first ? [first.city, first.country].filter(Boolean).join(", ") || "–" : "–"} />;
    }
    default:
      return <Metric label="Hops" value={run?.hop_count ? String(run.hop_count) : "–"} />;
  }
}

function Metric({ label, value, unit, color }: { label: string; value: string; unit?: string; color?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="num truncate text-base font-semibold leading-tight" style={{ color }}>
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

function TargetTable({ list, now, onEdit, onClone, onDelete, onToggle, onToggleNotify, onRun }: { list: Target[]; now: number; onEdit: (t: Target) => void; onClone: (t: Target) => void; onDelete: (t: Target) => void; onToggle: (t: Target) => void; onToggleNotify: (t: Target) => void; onRun: (t: Target) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="table num">
        <thead>
          <tr>
            <th>Target</th>
            <th>Status</th>
            <th className="text-right">Latency (ms)</th>
            <th className="text-right">Best (ms)</th>
            <th className="text-right">Worst (ms)</th>
            <th className="text-right">Loss</th>
            <th className="text-right">Hops / HTTP</th>
            <th className="text-right">Avail 24h</th>
            <th className="text-right">Avg 24h (ms)</th>
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
                  <div className="flex items-center gap-1.5 font-mono text-xs text-faint"><TypeBadge type={t.type} /><span className="truncate max-w-[220px]" title={t.host}>{hostLabel(t.host)}</span></div>
                </td>
                <td><div className="flex items-center gap-1.5"><StatusBadge status={status} running={t.running} />{!t.notify && <MutedBadge />}</div></td>
                <td className="text-right font-semibold">{run?.reached ? fmtNum(run.avg_ms) : "–"}</td>
                <td className="text-right text-muted">{run?.reached ? fmtNum(run.best_ms) : "–"}</td>
                <td className="text-right text-muted">{run?.reached ? fmtNum(run.worst_ms) : "–"}</td>
                <td className="text-right font-semibold" style={{ color: (run?.loss_pct ?? 0) > 0 ? lossColor(run?.loss_pct) : undefined }}>{fmtPct(run?.loss_pct)}</td>
                <td className="text-right">{isPathProbe(t.type, t.options) ? run?.hop_count || "–" : latencyLabel(t.type, t.options) === "Response" ? String((run?.details as Record<string, unknown> | null)?.status ?? "–") : "–"}</td>
                <td className="text-right">{fmtPct(t.stats_24h.availability_pct)}</td>
                <td className="text-right text-muted">{fmtNum(t.stats_24h.avg_ms)}</td>
                <td><Sparkline points={t.sparkline} width={120} height={26}  /></td>
                <td><StatusStrip buckets={t.timeline.buckets} bucketSec={t.timeline.bucket_sec} since={t.timeline.since} height={10} className="w-36" /></td>
                <td className="text-muted">{fmtDuration(t.interval_sec)}</td>
                <td className="text-muted">{run ? relTime(run.started_at, now) : "–"}</td>
                <td>
                  <div className="flex items-center gap-1">
                    <button className="btn btn-ghost btn-sm" disabled={t.running} onClick={() => onRun(t)}><Play size={14} />Run now</button>
                    <TargetActions name={t.name} enabled={t.enabled} notify={t.notify} onEdit={() => onEdit(t)} onClone={() => onClone(t)} onDelete={() => onDelete(t)} onToggle={() => onToggle(t)} onToggleNotify={() => onToggleNotify(t)} />
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
