import type { ReactNode } from "react";
import { classNames } from "../utils";

export function StatTile({
  label,
  value,
  sub,
  tone,
  icon,
  className,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "up" | "degraded" | "down" | "accent" | "muted";
  icon?: ReactNode;
  className?: string;
}) {
  const color = tone ? `var(--${tone === "muted" ? "text-muted" : tone})` : "var(--text)";
  return (
    <div className={classNames("card px-4 py-3 min-w-0", className)}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted truncate">{label}</div>
        {icon && <span className="text-faint">{icon}</span>}
      </div>
      <div className="num mt-1 text-2xl font-semibold leading-tight truncate" style={{ color }}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-faint truncate">{sub}</div>}
    </div>
  );
}
