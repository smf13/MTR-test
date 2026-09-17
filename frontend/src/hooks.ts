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
  // Bumped whenever the deps change or the hook unmounts. A response from an older generation is
  // discarded, and a request still in flight for the old parameters is never reused for the new ones
  // (which used to leave the previous target's or range's data on screen until the next tick).
  const generation = useRef(0);
  const inflight = useRef<{ generation: number; promise: Promise<void> } | null>(null);

  const refresh = useCallback(async () => {
    const gen = generation.current;
    if (inflight.current && inflight.current.generation === gen) return inflight.current.promise;
    const promise = (async () => {
      try {
        const result = await fetcherRef.current();
        if (gen !== generation.current) return;
        setData(result);
        setError(null);
        setLastUpdated(Date.now());
      } catch (e) {
        if (gen !== generation.current) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (gen === generation.current) setLoading(false);
        if (inflight.current?.generation === gen) inflight.current = null;
      }
    })();
    inflight.current = { generation: gen, promise };
    return promise;
  }, []);

  useEffect(() => {
    generation.current += 1;
    inflight.current = null;
    setLoading(true);
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, intervalMs);
    const onVis = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      generation.current += 1;
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

export type Theme = "dark" | "oled" | "light";
export const THEMES: { value: Theme; label: string; description: string }[] = [
  { value: "dark", label: "Dark", description: "Navy surfaces, easy on the eyes" },
  { value: "oled", label: "OLED", description: "True black backgrounds for OLED displays" },
  { value: "light", label: "Light", description: "Bright, for well-lit rooms" },
];

function readTheme(): Theme {
  const t = document.documentElement.dataset.theme;
  return t === "light" || t === "oled" ? t : "dark";
}

export function useTheme(): [Theme, (t: Theme) => void, () => void] {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("mtr-tracker.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  // Cycle dark -> oled -> light -> dark (used by the compact mobile button).
  const cycle = useCallback(() => setTheme((t) => THEMES[(THEMES.findIndex((x) => x.value === t) + 1) % THEMES.length].value), []);
  return [theme, setTheme, cycle];
}
