import { useSnappyChoice } from "../hooks";
import { RANGES } from "../utils";

export function RangePicker({ value: current, onChange }: { value: string; onChange: (v: string) => void }) {
  const [value, choose] = useSnappyChoice(current, onChange);
  return (
    <div className="seg" role="tablist" aria-label="Time range">
      {RANGES.map((r) => (
        <button key={r.value} data-active={value === r.value} onClick={() => choose(r.value)} role="tab" aria-selected={value === r.value}>
          {r.label}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({ value: current, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  const [value, choose] = useSnappyChoice(current, onChange);
  return (
    <div className="seg">
      {options.map((o) => (
        <button type="button" aria-pressed={value === o.value} key={o.value} data-active={value === o.value} onClick={() => choose(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
