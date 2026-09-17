import { useId, useRef } from "react";

/** Arrow-key navigation with native buttons, also usable on touch screens. */
export function Tabs<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  return <div ref={ref} role="tablist" aria-label={label} className="flex overflow-x-auto border-b border-border" onKeyDown={(e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const current = options.findIndex((option) => option.value === value);
    const next = e.key === "Home" ? 0 : e.key === "End" ? options.length - 1 : (current + (e.key === "ArrowRight" ? 1 : -1) + options.length) % options.length;
    onChange(options[next].value);
    ref.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  }}>
    {options.map((option) => <button key={option.value} id={`${id}-${option.value}`} type="button" role="tab" aria-selected={value === option.value} tabIndex={value === option.value ? 0 : -1} className={`min-h-11 shrink-0 border-b-2 px-4 py-3 text-sm font-medium transition-colors ${value === option.value ? "border-accent text-accent" : "border-transparent text-muted hover:text-text"}`} onClick={() => onChange(option.value)}>{option.label}</button>)}
  </div>;
}
