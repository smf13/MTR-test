import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check } from "lucide-react";
import { api } from "../api";
import { usePoll } from "../hooks";
import { TAG_PRESETS, autoTagColor, classNames, sortTags, tagChipStyle } from "../utils";

/*
 * Tag colours are global (a tag looks the same on every target) and live in settings.tag_colors.
 * The provider polls GET /api/tags so every page can render chips without loading the settings.
 */

interface TagColorsState {
  /** Explicit colours (tag -> #rrggbb) for tags currently in use; tags without an entry get an automatic colour. */
  colors: Record<string, string>;
  /** Every tag used by at least one target, sorted. */
  known: string[];
  refresh: () => Promise<void>;
}

const Ctx = createContext<TagColorsState>({ colors: {}, known: [], refresh: async () => {} });

export function TagColorsProvider({ children }: { children: ReactNode }) {
  const tags = usePoll(() => api.tags(), 60000);
  const value = useMemo<TagColorsState>(() => {
    const colors: Record<string, string> = {};
    (tags.data ?? []).forEach((t) => {
      if (t.color) colors[t.name] = t.color;
    });
    return { colors, known: (tags.data ?? []).map((t) => t.name), refresh: tags.refresh };
  }, [tags.data, tags.refresh]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTagColors(): TagColorsState {
  return useContext(Ctx);
}

export function TagChip({ tag, color, size = "sm", count, title, onClick, className }: { tag: string; color?: string | null; size?: "xs" | "sm"; count?: number; title?: string; onClick?: () => void; className?: string }) {
  const hex = color || autoTagColor(tag);
  const cls = classNames(
    "inline-flex max-w-full items-center gap-1 rounded border font-medium leading-none",
    size === "xs" ? "px-1.5 py-[3px] text-xs" : "px-2 py-1 text-xs",
    onClick && "cursor-pointer transition-[filter] hover:brightness-110",
    className,
  );
  const body = (
    <>
      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: hex }} />
      <span className="truncate">{tag}</span>
      {count !== undefined && <span className="opacity-60">×{count}</span>}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={cls} style={tagChipStyle(hex)} title={title} onClick={onClick}>
        {body}
      </button>
    );
  }
  return (
    <span className={cls} style={tagChipStyle(hex)} title={title}>
      {body}
    </span>
  );
}

/** Sorted, coloured chips for a target; shows "+N" when there are more than `max`. */
export function TagList({ tags, colors, max, size = "sm", className }: { tags: string[]; colors?: Record<string, string>; max?: number; size?: "xs" | "sm"; className?: string }) {
  const sorted = sortTags(tags);
  if (!sorted.length) return null;
  const shown = max ? sorted.slice(0, max) : sorted;
  const rest = sorted.length - shown.length;
  return (
    <span className={classNames("inline-flex flex-wrap items-center gap-1", className)}>
      {shown.map((tag) => (
        <TagChip key={tag} tag={tag} color={colors?.[tag]} size={size} />
      ))}
      {rest > 0 && (
        <span className="text-xs text-faint" title={sorted.slice(shown.length).join(", ")}>
          +{rest}
        </span>
      )}
    </span>
  );
}

/**
 * A chip that opens a small colour popover: preset swatches, a custom colour input and "Automatic".
 * `value` is the explicit colour (null = automatic); `onChange(null)` goes back to the automatic colour.
 */
export function TagColorPicker({ tag, value, count, onChange }: { tag: string; value: string | null; count?: number; onChange: (hex: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const effective = value || autoTagColor(tag);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    // Capture phase so Escape closes only the popover and not a surrounding modal.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative inline-block">
      <TagChip tag={tag} color={effective} count={count} title={`${value ? "Custom" : "Automatic"} colour · click to change`} onClick={() => setOpen((o) => !o)} />
      {open && (
        <div className="card absolute left-0 top-full z-50 mt-1 w-60 p-2.5 shadow-lg" style={{ borderColor: "var(--border-strong)" }} role="dialog" aria-label={`Colour for tag ${tag}`}>
          <div className="mb-2 flex items-center justify-between gap-2 text-xs">
            <span className="truncate font-semibold text-muted">
              Colour for <span className="font-mono text-text">{tag}</span>
            </span>
            <span className="shrink-0 text-faint">{value ? "custom" : "automatic"}</span>
          </div>
          <div className="grid grid-cols-6 gap-1.5">
            {TAG_PRESETS.map((p) => {
              const selected = p.hex === effective;
              return (
                <button
                  key={p.hex}
                  type="button"
                  className="flex h-7 items-center justify-center rounded-md transition-transform hover:scale-105"
                  style={{ background: p.hex, outline: selected ? "2px solid var(--text)" : "none", outlineOffset: 1 }}
                  title={p.name}
                  aria-label={p.name}
                  aria-pressed={selected}
                  onClick={() => onChange(p.hex)}
                >
                  {selected && <Check size={13} color="#fff" strokeWidth={3} />}
                </button>
              );
            })}
          </div>
          <div className="mt-2.5 flex items-center justify-between gap-2">
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
              <input type="color" className="h-6 w-8 cursor-pointer rounded border-0 bg-transparent p-0" value={effective} onChange={(e) => onChange(e.target.value.toLowerCase())} aria-label="Custom colour" />
              Custom
            </label>
            <button type="button" className="btn btn-sm" disabled={!value} onClick={() => onChange(null)}>
              Automatic
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
