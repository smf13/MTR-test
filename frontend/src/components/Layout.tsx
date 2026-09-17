import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { Activity, Bell, LayoutDashboard, Menu, Moon, MoonStar, Settings, Sun, Radar, X, FlaskConical, KeyRound } from "lucide-react";
import { useTheme, usePoll, THEMES } from "../hooks";
import { api, AUTH_REQUIRED_EVENT, getApiToken, setApiToken } from "../api";
import { Modal } from "./Modal";
import { classNames } from "../utils";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/events", label: "Events", icon: Bell, end: false },
  { to: "/trace", label: "Quick trace", icon: Radar, end: false },
  { to: "/settings", label: "Settings", icon: Settings, end: false },
];

export function Layout({ children }: { children: ReactNode }) {
  const [theme, setTheme, cycleTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const status = usePoll(() => api.status(), 15000);
  const s = status.data;
  const [tokenPrompt, setTokenPrompt] = useState(false);
  const [tokenDraft, setTokenDraft] = useState("");
  useEffect(() => {
    const onAuth = () => {
      setTokenDraft(getApiToken());
      setTokenPrompt(true);
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuth);
  }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

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
          {n.to === "/events" && s && s.events_24h > 0 && <span className="ml-auto rounded-full px-1.5 text-xs font-semibold" style={{ background: "var(--paused-soft)", color: "var(--text-muted)" }}>{s.events_24h}</span>}
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
          <div className="seg w-full" role="radiogroup" aria-label="Theme">
            {THEMES.map((t) => (
              <button key={t.value} className="flex-1" data-active={theme === t.value} onClick={() => setTheme(t.value)} title={t.description} role="radio" aria-checked={theme === t.value}>
                {t.label}
              </button>
            ))}
          </div>
          <div className="px-1">MTR Tracker {s?.version ?? ""}</div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* The menu panel is absolutely positioned inside the sticky header, so it opens right under the
            bar wherever the page is scrolled instead of being inserted into the flow at the top of the page. */}
        <header className="sticky top-0 z-30 border-b border-border bg-bg-elev/90 backdrop-blur lg:hidden">
          <div className="flex items-center gap-3 px-4 py-2.5">
            <button className="btn btn-ghost btn-sm" onClick={() => setOpen((o) => !o)} aria-label="Menu" aria-expanded={open}>
              {open ? <X size={18} /> : <Menu size={18} />}
            </button>
            <Brand compact />
            <button className="btn btn-ghost btn-sm ml-auto" onClick={cycleTheme} aria-label="Switch theme" title={`Theme: ${theme}`}>
              {theme === "dark" ? <MoonStar size={16} /> : theme === "oled" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
          {open && (
            <div className="absolute inset-x-0 top-full max-h-[calc(100vh-3.5rem)] overflow-y-auto border-b border-border bg-bg-elev px-4 py-3 shadow-lg">
              {nav}
              {s?.simulate && <div className="mt-2 text-xs font-medium text-degraded">Simulation mode: no real packets are sent.</div>}
            </div>
          )}
        </header>
        {open && <div className="fixed inset-0 z-20 bg-black/20 lg:hidden" onClick={() => setOpen(false)} aria-hidden="true" />}
        <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5 sm:px-6">{children}</main>
      </div>
      <Modal
        open={tokenPrompt}
        onClose={() => setTokenPrompt(false)}
        title={<span className="inline-flex items-center gap-2"><KeyRound size={16} /> API token required</span>}
        footer={
          <>
            <button className="btn" onClick={() => setTokenPrompt(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={() => { setApiToken(tokenDraft.trim()); setTokenPrompt(false); }}>Save token in this browser</button>
          </>
        }
      >
        <p className="text-sm text-muted">This server protects changes with an API token (MTR_TRACKER_API_TOKEN). Enter it once; it is kept in this browser's local storage and sent with every write. Then retry the action.</p>
        <input className="input mt-3 font-mono" type="password" value={tokenDraft} onChange={(e) => setTokenDraft(e.target.value)} placeholder="token" autoFocus onKeyDown={(e) => { if (e.key === "Enter") { setApiToken(tokenDraft.trim()); setTokenPrompt(false); } }} />
      </Modal>
    </div>
  );
}

function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg" style={{ background: "var(--accent)" }}>
        <svg viewBox="0 0 64 64" width="20" height="20">
          <circle cx="14" cy="44" r="7" fill="var(--accent-fg)" />
          <circle cx="32" cy="24" r="7" fill="var(--accent-fg)" />
          <circle cx="50" cy="38" r="7" fill="var(--accent-fg)" />
          <path d="M14 44 L32 24 L50 38" stroke="var(--accent-fg)" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className={classNames("font-semibold tracking-tight", compact ? "text-base" : "text-lg")}>MTR Tracker</span>
    </Link>
  );
}
