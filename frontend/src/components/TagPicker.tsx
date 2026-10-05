import { useEffect, useMemo, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { ConfirmDialog } from "./Modal";
import { TagChip } from "./Tags";
import type { GroupConfirm } from "./GroupPicker";
import { classNames, sortTags } from "../utils";

/** Option values that can never be a tag: the name field strips control characters, so these cannot collide. */
const NEW_TAG = "\u0001new";
const PLACEHOLDER = "\u0001choose";

/** The backend's limits (`models.MAX_TAGS`, `MAX_TAG_LENGTH`). */
export const MAX_TAGS = 20;
export const MAX_TAG_LENGTH = 40;

/** The same cleaning the backend applies to one tag (`models._clean_tags_value`): trimmed, at most 40 characters. */
export const cleanTagName = (v: string) => v.trim().slice(0, MAX_TAG_LENGTH);

/** Comma-separated text -> cleaned, de-duplicated tags in the order typed ("wan, isp-a" gives two). */
export function parseTagNames(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(",")) {
    const t = cleanTagName(raw);
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

/** A chip with a button that removes it. `children` is the chip itself (a plain one, or the colour picker's). */
export function RemovableTag({ tag, onRemove, disabled, children }: { tag: string; onRemove: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      {children}
      <button type="button" className="rounded p-0.5 text-faint transition-colors hover:bg-surface-2 hover:text-down disabled:opacity-40" aria-label={`Remove tag ${tag}`} title={`Remove tag ${tag}`} disabled={disabled} onClick={onRemove}>
        <X size={12} />
      </button>
    </span>
  );
}

/**
 * The target form's Tags field: the tags chosen so far (each removable, `renderTag` swaps in the colour picker), a
 * dropdown of the tags already in use that are not chosen yet, and **New tag…**, which reveals a text field. Several
 * new tags can be typed at once, separated by commas. Enter adds them (it never submits the surrounding form).
 */
export function TagSelect({ id, value, known, onChange, renderTag, note }: { id?: string; value: string[]; known: string[]; onChange: (tags: string[]) => void; renderTag?: (tag: string) => ReactNode; note?: ReactNode }) {
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  const chosen = useMemo(() => sortTags(value), [value]);
  const choices = useMemo(() => sortTags(known.filter((k) => !value.includes(k))), [known, value]);
  const full = value.length >= MAX_TAGS;

  const add = (names: string[]) => {
    const fresh = names.filter((n) => !value.includes(n));
    if (fresh.length) onChange([...value, ...fresh].slice(0, MAX_TAGS));
  };
  const commitDraft = () => {
    add(parseTagNames(draft));
    setDraft("");
    setCreating(false);
  };

  return (
    <>
      {chosen.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
          {chosen.map((tag) => (
            <RemovableTag key={tag} tag={tag} onRemove={() => onChange(value.filter((v) => v !== tag))}>
              {renderTag ? renderTag(tag) : <TagChip tag={tag} />}
            </RemovableTag>
          ))}
        </div>
      )}
      <select
        id={id}
        className="input"
        disabled={full}
        value={creating ? NEW_TAG : PLACEHOLDER}
        onChange={(e) => {
          const v = e.target.value;
          if (v === NEW_TAG) setCreating(true);
          else if (v !== PLACEHOLDER) add([v]);
        }}
      >
        <option value={PLACEHOLDER} disabled>{full ? `Limit of ${MAX_TAGS} tags reached` : choices.length ? "Add a tag…" : "No other tags yet"}</option>
        {choices.map((t) => <option key={t} value={t}>{t}</option>)}
        <option value={NEW_TAG}>New tag…</option>
      </select>
      {creating && !full && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <input
            className="input min-w-0 flex-1"
            aria-label="New tag name"
            autoFocus
            value={draft}
            maxLength={200}
            placeholder="e.g. isp-a, critical"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                // The field sits inside the target form: Enter adds the tag instead of saving the target.
                e.preventDefault();
                if (parseTagNames(draft).length) commitDraft();
              } else if (e.key === "Escape") {
                e.stopPropagation();
                setDraft("");
                setCreating(false);
              }
            }}
          />
          <button type="button" className="btn btn-sm btn-primary" disabled={!parseTagNames(draft).length} onClick={commitDraft}>Add</button>
          <button type="button" className="btn btn-sm" onClick={() => { setDraft(""); setCreating(false); }}>Cancel</button>
        </div>
      )}
      <div className="help">
        Pick a tag already in use or choose <em>New tag…</em> (separate several with commas). {value.length}/{MAX_TAGS} used, up to {MAX_TAG_LENGTH} characters each.{note ? <> {note}</> : null}
      </div>
    </>
  );
}

/** One entry of a `TagMenu`: the tag and an optional note shown after it ("2 of 5"). */
export interface TagChoice {
  name: string;
  note?: string;
}

/**
 * A tag chooser that applies the choice at once, for the dashboard's selection bar ("Add tag…", "Remove tag…") and the
 * target page's "Add tag…". With `allowNew`, **New tag…** swaps the dropdown for a name field with Save and Cancel
 * (Enter saves, Escape cancels; commas separate several tags). With `confirm`, every choice first opens a
 * confirmation built from it; Cancel or Escape changes nothing.
 */
export function TagMenu({
  label,
  choices,
  placeholder,
  allowNew = false,
  disabled,
  className,
  onPick,
  confirm,
}: {
  label: string;
  choices: TagChoice[];
  placeholder: string;
  allowNew?: boolean;
  disabled?: boolean;
  className?: string;
  /** Apply the choice; resolve false (or reject) when it failed. */
  onPick: (tags: string[]) => Promise<boolean>;
  /** The confirmation for a choice (the caller knows which tags are new). Without it a choice applies at once. */
  confirm?: (tags: string[]) => GroupConfirm;
}) {
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  // The choice waiting for its confirmation.
  const [asking, setAsking] = useState<string[] | null>(null);

  const apply = (tags: string[]) => {
    if (!tags.length) return;
    if (confirm) setAsking(tags);
    else void onPick(tags).catch(() => false);
  };
  const question = asking !== null && confirm ? confirm(asking) : null;
  const dialog = (
    <ConfirmDialog
      open={question !== null}
      title={question?.title ?? ""}
      message={question?.message ?? ""}
      confirmLabel={question?.confirmLabel}
      onConfirm={() => {
        const tags = asking;
        setAsking(null);
        if (tags) void onPick(tags).catch(() => false);
      }}
      onCancel={() => setAsking(null)}
    />
  );

  if (creating) {
    const names = parseTagNames(draft);
    return (
      <>
        <form
          className={classNames("inline-flex flex-wrap items-center gap-1.5", className)}
          onSubmit={(e) => {
            e.preventDefault();
            if (!names.length) return;
            setCreating(false);
            apply(names);
          }}
        >
          <input
            className="input w-48 py-1 text-xs"
            aria-label="New tag name"
            autoFocus
            value={draft}
            maxLength={200}
            placeholder="New tag name"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setCreating(false);
              }
            }}
          />
          <button type="submit" className="btn btn-primary btn-sm" disabled={!names.length}>Save</button>
          <button type="button" className="btn btn-sm" onClick={() => setCreating(false)}>Cancel</button>
        </form>
        {dialog}
      </>
    );
  }

  return (
    <>
      <select
        aria-label={label}
        className={classNames("input w-auto max-w-[16rem] py-1 text-xs", className)}
        disabled={disabled || (!choices.length && !allowNew)}
        value={PLACEHOLDER}
        onChange={(e) => {
          const v = e.target.value;
          if (v === PLACEHOLDER) return;
          if (v === NEW_TAG) {
            setDraft("");
            setCreating(true);
            return;
          }
          apply([v]);
        }}
      >
        <option value={PLACEHOLDER} disabled>{placeholder}</option>
        {choices.map((c) => <option key={c.name} value={c.name}>{c.note ? `${c.name} (${c.note})` : c.name}</option>)}
        {allowNew && <option value={NEW_TAG}>New tag…</option>}
      </select>
      {dialog}
    </>
  );
}

/**
 * The target page's tags (`name` is the target's): coloured chips that can be removed, and an "Add tag…" dropdown
 * (existing tags, **New tag…**). Each change is written through `onChange`, which resolves false when the write
 * failed; adding happens at once, removing first asks for confirmation (Cancel or Escape keeps the tag). The picked
 * list shows until the refreshed target takes over, and the controls wait for the write.
 */
export function TagEditor({ name, tags, known, colors, onChange }: { name: string; tags: string[]; known: string[]; colors?: Record<string, string>; onChange: (tags: string[]) => Promise<boolean> }) {
  const [pending, setPending] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  // The tag waiting for its removal to be confirmed.
  const [removing, setRemoving] = useState<string | null>(null);
  const key = tags.join("\u0000");
  useEffect(() => setPending(null), [key]);
  const shown = pending ?? tags;
  const choices = useMemo(() => sortTags(known.filter((k) => !shown.includes(k))).map((name) => ({ name })), [known, shown]);

  const write = async (next: string[]): Promise<boolean> => {
    const sorted = sortTags(next);
    setPending(sorted);
    setBusy(true);
    try {
      // On success the picked list stays until the refreshed target brings it as `tags`.
      const ok = await onChange(sorted);
      if (!ok) setPending(null);
      return ok;
    } catch {
      setPending(null);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const add = async (names: string[]) => {
    const next = [...shown, ...names.filter((n) => !shown.includes(n))].slice(0, MAX_TAGS);
    return next.length === shown.length || write(next);
  };
  const question = removing !== null ? tagConfirm([name], [removing], false) : null;

  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid="tag-editor">
      {sortTags(shown).map((tag) => (
        <RemovableTag key={tag} tag={tag} disabled={busy} onRemove={() => setRemoving(tag)}>
          <TagChip tag={tag} color={colors?.[tag]} size="xs" />
        </RemovableTag>
      ))}
      {shown.length < MAX_TAGS ? (
        <TagMenu label="Add tag" placeholder="Add tag…" choices={choices} allowNew disabled={busy} onPick={add} />
      ) : (
        <span className="text-xs text-faint">Limit of {MAX_TAGS} tags</span>
      )}
      <ConfirmDialog
        open={question !== null}
        title={question?.title ?? ""}
        message={question?.message ?? ""}
        confirmLabel={question?.confirmLabel}
        onConfirm={() => {
          const tag = removing;
          setRemoving(null);
          if (tag !== null) void write(shown.filter((t) => t !== tag));
        }}
        onCancel={() => setRemoving(null)}
      />
    </span>
  );
}

/**
 * What a `TagMenu` asks before it applies a bulk choice: `names` of the targets, `tags` being added or removed,
 * `created` the ones that do not exist yet.
 */
export function tagConfirm(names: string[], tags: string[], adding: boolean, created: string[] = []): GroupConfirm {
  const one = names.length === 1;
  const subject = one ? names[0] : `${names.length} targets`;
  const noun = (list: string[]) => (list.length === 1 ? "tag" : "tags");
  const existing = tags.filter((t) => !created.includes(t));
  const shown = names.slice(0, 5);
  return {
    title: `${adding ? "Add" : "Remove"} ${noun(tags)} ${tags.join(", ")} ${adding ? "to" : "from"} ${subject}?`,
    confirmLabel: adding ? `Add ${noun(tags)}` : `Remove ${noun(tags)}`,
    message: (
      <>
        <p>
          {adding && created.length > 0 && <>This creates the new {noun(created)} <strong>{created.join(", ")}</strong>. </>}
          {adding && existing.length > 0 && <>{one ? "It gets" : "They get"} the {noun(existing)} <strong>{existing.join(", ")}</strong>. </>}
          {!adding && <>{one ? "It loses" : "They lose"} the {noun(tags)} <strong>{tags.join(", ")}</strong>. </>}
          {one ? "Its" : "Their"} other tags stay.
        </p>
        {names.length > 1 && (
          <p className="mt-2" data-testid="tag-targets">
            {shown.join(", ")}{names.length > shown.length ? ` and ${names.length - shown.length} more` : ""}
          </p>
        )}
      </>
    ),
  };
}
