import { useEffect, useState } from "react";
import { Bell, Trash2 } from "lucide-react";
import { api } from "../api";
import { usePoll, useNow } from "../hooks";
import { EventsList, EVENT_KIND_LABEL } from "../components/EventsList";
import { RangePicker, Segmented } from "../components/RangePicker";
import { EmptyState, ErrorBanner } from "../components/EmptyState";
import { ConfirmDialog } from "../components/Modal";
import { Pager } from "./TargetDetail";
import { useToast } from "../components/Toast";

const PAGE = 50;

export function Events() {
  const now = useNow();
  const toast = useToast();
  const [range, setRange] = useState("7d");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const events = usePoll(() => api.events({ limit: PAGE, offset: page * PAGE, range, kind: kind || undefined }), 15000, [range, kind, page]);
  useEffect(() => setPage(0), [range, kind]);

  const clear = async () => {
    await api.clearEvents();
    setConfirmClear(false);
    toast("Event log cleared", "info");
    void events.refresh();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Events</h1>
          <p className="text-sm text-muted">State changes, threshold alerts and route changes across all targets.</p>
        </div>
        <button className="btn btn-danger" onClick={() => setConfirmClear(true)} disabled={!events.data?.total}><Trash2 size={15} /> Clear log</button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <RangePicker value={range} onChange={setRange} />
          <Segmented value={kind} onChange={setKind} options={[{ value: "", label: "All" }, ...Object.entries(EVENT_KIND_LABEL).map(([value, label]) => ({ value, label }))]} />
        </div>
        <Pager page={page} pageSize={PAGE} total={events.data?.total ?? 0} onChange={setPage} />
      </div>
      {events.error && <ErrorBanner message={events.error} />}
      <div className="card overflow-hidden">
        {events.data && events.data.items.length === 0 ? (
          <EmptyState icon={<Bell size={32} />} title="No events in this range" body="Events appear when a target changes state, crosses an alert threshold, or its route changes." />
        ) : (
          <EventsList events={events.data?.items ?? []} now={now} />
        )}
      </div>
      <ConfirmDialog open={confirmClear} title="Clear all events?" message="This deletes the entire event log for every target. Runs and hop data are kept." confirmLabel="Clear events" danger onConfirm={clear} onCancel={() => setConfirmClear(false)} />
    </div>
  );
}
