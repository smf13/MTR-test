import { X } from "lucide-react";
import { RemovableTag } from "./TagPicker";
import { Segmented } from "./RangePicker";
import { TagChip } from "./Tags";

export type TagMatch = "any" | "all";

/** A tag in use with the number of targets that carry it. */
export interface TagCount {
  name: string;
  count: number;
}

const PLACEHOLDER = "\u0001choose";

/**
 * The dashboard's **Filter by tag** dropdown: the tags in use that the filter does not use yet, each with its number
 * of targets. Picking one adds it to the filter (`ActiveTagFilter` shows and edits the result).
 */
export function TagFilterSelect({ tags, selected, onAdd }: { tags: TagCount[]; selected: readonly string[]; onAdd: (tag: string) => void }) {
  const open = tags.filter((t) => !selected.includes(t.name));
  // Nothing to filter by until some target has a tag, and nothing to add once the filter uses them all.
  if (!tags.length || (!open.length && !selected.length)) return null;
  return (
    <select
      aria-label="Filter by tag"
      className="input w-auto max-w-[14rem]"
      disabled={!open.length}
      value={PLACEHOLDER}
      onChange={(e) => e.target.value !== PLACEHOLDER && onAdd(e.target.value)}
    >
      <option value={PLACEHOLDER} disabled>Filter by tag…</option>
      {open.map((t) => <option key={t.name} value={t.name}>{t.name} ({t.count})</option>)}
    </select>
  );
}

/**
 * The tags the filter uses, as removable chips, plus Any/All for how several combine and a button that clears them.
 * Renders nothing while the filter has no tags.
 */
export function ActiveTagFilter({ selected, colors, mode, onMode, onRemove, onClear }: { selected: readonly string[]; colors: Record<string, string>; mode: TagMatch; onMode: (mode: TagMatch) => void; onRemove: (tag: string) => void; onClear: () => void }) {
  if (!selected.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2" role="group" aria-label="Active tag filter">
      <span className="text-xs font-medium text-muted">Tags</span>
      <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1.5">
        {selected.map((tag) => (
          <RemovableTag key={tag} tag={tag} onRemove={() => onRemove(tag)}>
            <TagChip tag={tag} color={colors[tag]} />
          </RemovableTag>
        ))}
      </span>
      {selected.length > 1 && (
        <span className="inline-flex items-center gap-2 text-xs text-muted">
          Match
          <Segmented<TagMatch> value={mode} onChange={onMode} options={[{ value: "any", label: "Any tag" }, { value: "all", label: "All tags" }]} />
        </span>
      )}
      <button type="button" className="btn btn-ghost btn-sm" onClick={onClear}><X size={13} /> Clear tags</button>
    </div>
  );
}
