export function Pager({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center gap-2 text-xs text-muted">
      <span className="num">{total ? `${page * pageSize + 1}–${Math.min(total, (page + 1) * pageSize)} of ${total}` : "0 results"}</span>
      <button className="btn btn-sm" disabled={page === 0} onClick={() => onChange(page - 1)}>Prev</button>
      <button className="btn btn-sm" disabled={page + 1 >= pages} onClick={() => onChange(page + 1)}>Next</button>
    </div>
  );
}
