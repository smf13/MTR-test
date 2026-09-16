import { useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { Activity, Bell, LayoutDashboard, Menu, Moon, Settings, Sun, Radar, X, FlaskConical } from "lucide-react";
import { useTheme, usePoll } from "../hooks";
import { api } from "../api";
import { classNames } from "../utils";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/events", label: "Events", icon: Bell, end: false },
  { to: "/trace", label: "Quick trace", icon: Radar, end: false },
  { to: "/settings", label: "Settings", icon: Settings, end: false },
];

export function Layout({ children }: { children: ReactNode }) {
  const [theme, toggleTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const status = usePoll(() => api.status(), 15000);
  const s = status.data;

  const nav = (
    <nav className="flex flex-col gap-1">
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.end}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            classNames("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors", isActive ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-text")
          }
        >
          <n.icon size={17} />
          {n.label}
          {n.to === "/events" && s && s.events_24h > 0 && <span className="ml-auto rounded-full px-1.5 text-[10px] font-semibold" style={{ background: "var(--paused-soft)", color: "var(--text-muted)" }}>{s.events_24h}</span>}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-bg-elev px-4 py-5 lg:flex">
        <Brand />
        <div className="mt-6">{nav}</div>
        <div className="mt-auto space-y-3 text-xs text-faint">
          {s && (
            <div className="rounded-lg border border-border p-3">
              <div className="flex items-center gap-1.5 font-medium text-muted">
                <Activity size={13} /> Engine
              </div>
              <div className="mt-1.5 grid grid-cols-2 gap-y-0.5">
                <span>Targets</span>
                <span className="num text-right text-text">{s.targets.enabled}/{s.targets.total}</span>
                <span>Probing</span>
                <span className="num text-right text-text">{s.active_runs.length}</span>
                <span>Runs 24h</span>
                <span className="num text-right text-text">{s.runs_24h.total}</span>
              </div>
              {s.simulate && (
                <div className="mt-2 flex items-center gap-1 font-medium text-degraded">
                  <FlaskConical size={12} /> Simulation mode
                </div>
              )}
            </div>
          )}
          <button className="btn w-full justify-center" onClick={toggleTheme}>
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
          <div className="px-1">HopWatch {s?.version ?? ""}</div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-border bg-bg-elev/90 px-4 py-2.5 backdrop-blur lg:hidden">
          <button className="btn btn-ghost btn-sm" onClick={() => setOpen((o) => !o)} aria-label="Menu">
            {open ? <X size={18} /> : <Menu size={18} />}
          </button>
          <Brand compact />
          <button className="btn btn-ghost btn-sm ml-auto" onClick={toggleTheme} aria-label="Toggle theme">
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </header>
        {open && (
          <div className="border-b border-border bg-bg-elev px-4 py-3 lg:hidden">
            {nav}
            {s?.simulate && <div className="mt-2 text-xs font-medium text-degraded">Simulation mode: no real packets are sent.</div>}
          </div>
        )}
        <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5 sm:px-6">{children}</main>
      </div>
    </div>
  );
}

function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg" style={{ background: "var(--accent)" }}>
        <svg viewBox="0 0 64 64" width="20" height="20">
          <circle cx="14" cy="44" r="7" fill="#fff" />
          <circle cx="32" cy="24" r="7" fill="#fff" />
          <circle cx="50" cy="38" r="7" fill="#fff" />
          <path d="M14 44 L32 24 L50 38" stroke="#fff" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className={classNames("font-semibold tracking-tight", compact ? "text-base" : "text-lg")}>HopWatch</span>
    </Link>
  );
}
