import type { Status } from "../api";
import { STATUS_LABEL, classNames } from "../utils";

const STYLES: Record<Status, string> = {
  up: "bg-up-soft text-up",
  degraded: "bg-degraded-soft text-degraded",
  down: "bg-down-soft text-down",
  pending: "bg-paused-soft text-paused",
  paused: "bg-paused-soft text-paused",
};

export function StatusDot({ status, pulse = false, size = 8 }: { status: Status; pulse?: boolean; size?: number }) {
  return (
    <span
      className={classNames("inline-block rounded-full shrink-0", pulse && "pulse")}
      style={{ width: size, height: size, background: `var(--${status === "pending" || status === "paused" ? "paused" : status})` }}
    />
  );
}

export function StatusBadge({ status, running = false, className }: { status: Status; running?: boolean; className?: string }) {
  return (
    <span className={classNames("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold", STYLES[status], className)}>
      <StatusDot status={status} pulse={running || status === "pending"} />
      {running ? "Probing" : STATUS_LABEL[status]}
    </span>
  );
}
