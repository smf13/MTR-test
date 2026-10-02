import { useEffect, useMemo, useState } from "react";
import { classNames } from "../utils";

/** Option values that can never be a group name: the backend collapses whitespace and stores no control characters from the UI. */
const NEW_GROUP = "\u0001new";
const PLACEHOLDER = "\u0001choose";

/** The same cleaning the backend applies (`models._clean_group_value`): one line, single spaces, at most 60 characters. */
export const cleanGroupName = (v: string) => v.trim().replace(/\s+/g, " ").slice(0, 60);

/** Group names in the dashboard's order (case-insensitive, exact-case variants uppercase first), `extra` included when set. */
export function groupChoices(groups: string[], extra = ""): string[] {
  const names = new Set(groups.filter(Boolean));
  if (extra) names.add(extra);
  return [...names].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }) || (a < b ? -1 : a > b ? 1 : 0));
}

/**
 * The target form's Group field: a dropdown of the groups in use, **No group**, and **New group…**, which reveals a
 * text field for the new name. The current value is always offered, even before the list of groups has loaded.
 */
export function GroupSelect({ id, value, groups, onChange }: { id?: string; value: string; groups: string[]; onChange: (value: string) => void }) {
  // Starts in "new" mode only when asked; a value that is simply not in a (still loading) list is offered as an option.
  const [creating, setCreating] = useState(false);
  const choices = useMemo(() => groupChoices(groups, creating ? "" : value), [groups, value, creating]);
  return (
    <>
      <select
        id={id}
        className="input"
        value={creating ? NEW_GROUP : value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === NEW_GROUP) {
            setCreating(true);
            onChange("");
          } else {
            setCreating(false);
            onChange(v);
          }
        }}
      >
        <option value="">No group</option>
        {choices.map((g) => <option key={g} value={g}>{g}</option>)}
        <option value={NEW_GROUP}>New group…</option>
      </select>
      {creating && (
        <input className="input mt-2" aria-label="New group name" autoFocus value={value} onChange={(e) => onChange(e.target.value)} placeholder="e.g. Branch offices" maxLength={60} />
      )}
    </>
  );
}

/**
 * A group chooser that applies the choice at once: the target page's group (`current` set) and the dashboard's
 * "Move to group" for a selection (`current` undefined, a placeholder shows and **Remove from group** ungroups).
 * **New group…** swaps the dropdown for a name field with Save and Cancel (Enter saves, Escape cancels).
 */
export function GroupMenu({
  label,
  groups,
  current,
  placeholder = "Move to group…",
  disabled,
  className,
  onPick,
}: {
  label: string;
  groups: string[];
  current?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** Apply the choice; resolve false (or reject) when it failed, so the target page shows the stored group again. */
  onPick: (group: string) => Promise<boolean>;
}) {
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  // The target page shows the picked group until the write returns and the refreshed target takes over.
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => setPending(null), [current]);
  const choices = useMemo(() => groupChoices(groups, pending ?? current ?? ""), [groups, current, pending]);

  const apply = (group: string) => {
    if (current !== undefined) {
      if (group === current) return;
      setPending(group);
    }
    // On success the pending name stays until the refreshed target brings it as `current`.
    onPick(group).then((ok) => !ok && setPending(null), () => setPending(null));
  };

  if (creating) {
    const name = cleanGroupName(draft);
    return (
      <form
        className={classNames("inline-flex flex-wrap items-center gap-1.5", className)}
        onSubmit={(e) => {
          e.preventDefault();
          if (!name) return;
          setCreating(false);
          apply(name);
        }}
      >
        <input
          className="input w-48 py-1 text-xs"
          aria-label="New group name"
          autoFocus
          value={draft}
          maxLength={60}
          placeholder="New group name"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              setCreating(false);
            }
          }}
        />
        <button type="submit" className="btn btn-primary btn-sm" disabled={!name}>Save</button>
        <button type="button" className="btn btn-sm" onClick={() => setCreating(false)}>Cancel</button>
      </form>
    );
  }

  return (
    <select
      aria-label={label}
      className={classNames("input w-auto max-w-[16rem] py-1 text-xs", className)}
      disabled={disabled}
      value={pending ?? current ?? PLACEHOLDER}
      onChange={(e) => {
        const v = e.target.value;
        if (v === PLACEHOLDER) return;
        if (v === NEW_GROUP) {
          setDraft("");
          setCreating(true);
          return;
        }
        apply(v);
      }}
    >
      {current === undefined && <option value={PLACEHOLDER} disabled>{placeholder}</option>}
      {current !== undefined && <option value="">No group</option>}
      {choices.map((g) => <option key={g} value={g}>{g}</option>)}
      {current === undefined && <option value="">Remove from group</option>}
      <option value={NEW_GROUP}>New group…</option>
    </select>
  );
}
