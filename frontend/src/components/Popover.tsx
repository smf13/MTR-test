import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Info, MoreHorizontal } from "lucide-react";

/** Portalled so menus stay visible inside scrolling tables and clipped cards. */
function Popover({ label, icon, menu = false, children }: { label: string; icon: ReactNode; menu?: boolean; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = () => { setOpen(false); trigger.current?.focus(); };

  useLayoutEffect(() => {
    if (!open || !trigger.current || !panel.current) return;
    const anchor = trigger.current.getBoundingClientRect();
    const box = panel.current.getBoundingClientRect();
    setPosition({
      left: Math.max(8, Math.min(anchor.right - box.width, window.innerWidth - box.width - 8)),
      top: anchor.bottom + box.height + 8 < window.innerHeight ? anchor.bottom + 6 : Math.max(8, anchor.top - box.height - 6),
    });
    if (menu) panel.current.querySelector<HTMLButtonElement>("button")?.focus();
    else panel.current.focus();
  }, [open, menu]);

  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      if (!panel.current?.contains(e.target as Node) && !trigger.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); setOpen(false); trigger.current?.focus(); }
    };
    const reposition = (e: Event) => {
      if (e.target instanceof Node && panel.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", key);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open]);

  return <>
    <button ref={trigger} type="button" className="icon-button" aria-label={label} aria-haspopup={menu ? "menu" : "dialog"} aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>{icon}</button>
    {open && createPortal(
      <div ref={panel} id={id} tabIndex={-1} role={menu ? "menu" : "dialog"} aria-label={label} className={menu ? "popover w-48 p-1" : "popover w-72 p-3 text-sm text-muted"} style={position}
        onBlur={(e) => { if (menu && !e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}
        onKeyDown={(e) => {
          if (!menu || !["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
          e.preventDefault();
          const items = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
          const index = items.indexOf(document.activeElement as HTMLButtonElement);
          const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (index + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
          items[next]?.focus();
        }}>
        {children(close)}
      </div>, document.body)}
  </>;
}

export function HelpTip({ label, children }: { label: string; children: ReactNode }) {
  return <Popover label={`About ${label}`} icon={<Info size={15} />}>{(close) => <>
    <div>{children}</div>
    <button type="button" className="mt-2 text-xs font-medium text-accent hover:underline" onClick={close}>Close help</button>
  </>}</Popover>;
}

export function ActionMenu({ label, actions }: { label: string; actions: { label: string; icon: ReactNode; onClick: () => void; danger?: boolean }[] }) {
  return <Popover label={label} icon={<MoreHorizontal size={18} />} menu>{(close) => actions.map((action) => (
    <button key={action.label} type="button" role="menuitem" className={`menu-item ${action.danger ? "text-down" : "text-text"}`} onClick={() => { close(); action.onClick(); }}>
      {action.icon}{action.label}
    </button>
  ))}</Popover>;
}
