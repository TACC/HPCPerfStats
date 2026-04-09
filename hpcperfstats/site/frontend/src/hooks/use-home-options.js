import { useCallback, useEffect } from "react";
import { api } from "../api";
import { useAsyncFetch } from "./useAsyncFetch";

/** Loads `/home/` JSON for search UIs (year/date lists, metrics, queues, states). */
export function useHomeOptions() {
  const fetchHomeOptions = useCallback(() => api.getHomeOptions(), []);
  const {
    data: options,
    error,
    loading,
    run,
  } = useAsyncFetch(fetchHomeOptions, null);

  useEffect(() => {
    run().catch(() => null);
  }, [run]);

  return { options, error, loading };
}
