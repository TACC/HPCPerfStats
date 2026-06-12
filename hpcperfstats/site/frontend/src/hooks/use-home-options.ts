import { useHomeRetrieve } from "@/api/generated/home/home";

/** Loads `/api/home/` JSON for search UIs (year/date lists, metrics, queues, states). */
export function useHomeOptions() {
  const { data: options, error, isLoading: loading } = useHomeRetrieve();
  return {
    options: options ?? null,
    error: error ? (error as Error).message : null,
    loading,
  };
}
