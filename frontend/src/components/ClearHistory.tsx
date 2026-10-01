import { useEffect, useState } from "react";
import { api, type HistoryDeleted } from "../api";
import { Modal } from "./Modal";
import { classNames } from "../utils";

type Scope = "failed" | "all";

/**
 * "Clear history…" from the target's action menu: delete only the failed runs (the usual clean-up after a target was
 * set up with a wrong keyword or JSON query) or the whole history. Both counts are fetched when the dialog opens so
 * the confirmation names exactly what goes.
 */
export function ClearHistoryDialog({ open, targetId, name, onClose, onDone }: { open: boolean; targetId: number; name: string; onClose: () => void; onDone: (r: HistoryDeleted) => void }) {
  const [scope, setScope] = useState<Scope>("failed");
  const [counts, setCounts] = useState<{ all: number; failed: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setScope("failed");
    setCounts(null);
    setError(null);
    Promise.all([api.runs(targetId, { limit: 1 }), api.runs(targetId, { limit: 1, status: "failed" })])
      .then(([all, failed]) => live && setCounts({ all: all.total, failed: failed.total }))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [open, targetId]);

  const n = counts ? counts[scope] : 0;
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone(await api.clearRuns(targetId, scope));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const option = (value: Scope, label: string, help: string) => (
    <label className={classNames("flex cursor-pointer items-start gap-2.5 rounded-lg border p-3", scope === value ? "border-accent" : "border-border")}>
      <input type="radio" name="clear-scope" className="mt-1" checked={scope === value} onChange={() => setScope(value)} />
      <span>
        <span className="block font-medium text-text">
          {label} <span className="num text-muted">({counts ? `${counts[value]} run${counts[value] === 1 ? "" : "s"}` : "…"})</span>
        </span>
        <span className="block text-xs text-muted">{help}</span>
      </span>
    </label>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Clear history of ${name}?`}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={confirm} disabled={busy || !counts || n === 0}>
            {busy ? "Deleting…" : `Delete ${n} run${n === 1 ? "" : "s"}`}
          </button>
        </>
      }
    >
      <div className="space-y-2 text-sm">
        {option("failed", "Failed runs only", "Runs with an error or a failed check, and the events they raised. Useful after fixing a wrong keyword or JSON query.")}
        {option("all", "Entire history", "Every run and every event of this target. The configuration stays.")}
        <p className="pt-1 text-xs text-muted">This cannot be undone. When the latest run is among them, the status shows pending until the next run.</p>
        {error && <p className="text-xs" style={{ color: "var(--down)" }}>{error}</p>}
      </div>
    </Modal>
  );
}
