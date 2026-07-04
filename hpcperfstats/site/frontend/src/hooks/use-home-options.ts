import { useHomeRetrieve } from "@/api/generated/home/home";
import { selectOrvalData } from "@/api/orval-response";

/** Loads `/api/home/` JSON for search UIs (year/date lists, metrics, queues, states). */
export function useHomeOptions() {
  const { data: options, error, isLoading } = useHomeRetrieve({
    query: { select: selectOrvalData },
  });
  const initialLoading = isLoading && !options;
  return {
    options: options ?? null,
    error: error ? (error as Error).message : null,
    initialLoading,
    /** @deprecated Prefer initialLoading per interactive-ready-controls.mdc */
    loading: initialLoading,
  };
}
