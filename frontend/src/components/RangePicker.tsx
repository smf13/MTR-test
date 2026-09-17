import { RANGES } from "../utils";

export function RangePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="seg" role="tablist" aria-label="Time range">
      {RANGES.map((r) => (
        <button key={r.value} data-active={value === r.value} onClick={() => onChange(r.value)} role="tab" aria-selected={value === r.value}>
          {r.label}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button type="button" aria-pressed={value === o.value} key={o.value} data-active={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
