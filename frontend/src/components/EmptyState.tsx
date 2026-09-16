import type { ReactNode } from "react";

export function EmptyState({ icon, title, body, action }: { icon?: ReactNode; title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="text-faint">{icon}</div>}
      <div className="text-sm font-semibold">{title}</div>
      {body && <div className="max-w-md text-sm text-muted">{body}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-lg border px-3 py-2 text-sm" style={{ borderColor: "var(--down)", background: "var(--down-soft)", color: "var(--down)" }}>
      {message}
    </div>
  );
}
