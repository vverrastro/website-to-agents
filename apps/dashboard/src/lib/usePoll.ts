import { useCallback, useEffect, useRef, useState } from "react";

/** Load data on mount and re-poll while `active` is true. */
export function usePoll<T>(load: () => Promise<T>, intervalMs: number, active: (data: T) => boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!data || !active(data)) return;
    const t = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(t);
  }, [data, active, intervalMs, refresh]);

  return { data, error, refresh };
}
