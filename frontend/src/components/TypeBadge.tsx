import { PROBE_TYPE_LABEL, type ProbeType } from "../api";

export function TypeBadge({ type }: { type: ProbeType }) {
  return (
    <span className="rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
      {PROBE_TYPE_LABEL[type] ?? type}
    </span>
  );
}
