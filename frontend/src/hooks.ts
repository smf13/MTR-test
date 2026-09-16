import { useCallback, useEffect, useRef, useState } from "react";

export interface PollState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
  lastUpdated: number | null;
}

/** Poll an async fetcher on an interval; pauses while the tab is hidden. */
export function usePoll<T>(fetcher: () => Promise<T>, intervalMs: number, deps: unknown[] = []): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const alive = useRef(true);
  const inflight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (inflight.current) return inflight.current;
    const p = (async () => {
      try {
        const result = await fetcherRef.current();
        if (!alive.current) return;
        setData(result);
        setError(null);
        setLastUpdated(Date.now());
      } catch (e) {
        if (!alive.current) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive.current) setLoading(false);
        inflight.current = null;
      }
    })();
    inflight.current = p;
    return p;
  }, []);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    void refresh();
    let timer: number | undefined;
    const schedule = () => {
      window.clearInterval(timer);
      timer = window.setInterval(() => {
        if (document.visibilityState === "visible") void refresh();
      }, intervalMs);
    };
    schedule();
    const onVis = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, refresh, ...deps]);

  return { data, error, loading, refresh, lastUpdated };
}

export function useNow(tickMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), tickMs);
    return () => window.clearInterval(t);
  }, [tickMs]);
  return now;
}

export function useLocalStorage<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (v: T) => {
      setValue(v);
      try {
        localStorage.setItem(key, JSON.stringify(v));
      } catch {
        /* ignore */
      }
    },
    [key],
  );
  return [value, set];
}

export type Theme = "dark" | "light";

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme === "light" ? "light" : "dark"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("mtr-tracker.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  const toggle = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  return [theme, toggle];
}
