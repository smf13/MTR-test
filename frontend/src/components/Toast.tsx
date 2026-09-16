import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";

type Kind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: Kind;
  text: string;
}

const Ctx = createContext<(text: string, kind?: Kind) => void>(() => {});

export function useToast() {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Kind = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  const value = useMemo(() => push, [push]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(92vw,380px)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="card pointer-events-auto fade-in flex items-start gap-2 px-3 py-2.5 text-sm"
            style={{
              borderColor: t.kind === "error" ? "var(--down)" : t.kind === "success" ? "var(--up)" : "var(--border-strong)",
            }}
          >
            {t.kind === "success" ? (
              <CheckCircle2 size={16} style={{ color: "var(--up)" }} className="mt-0.5 shrink-0" />
            ) : t.kind === "error" ? (
              <AlertTriangle size={16} style={{ color: "var(--down)" }} className="mt-0.5 shrink-0" />
            ) : (
              <Info size={16} style={{ color: "var(--accent)" }} className="mt-0.5 shrink-0" />
            )}
            <span className="flex-1 break-words">{t.text}</span>
            <button className="text-faint hover:text-text" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
