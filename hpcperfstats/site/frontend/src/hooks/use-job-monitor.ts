import { useJobMonitorRetrieve } from "@/api/generated/monitor/monitor";
import { getErrorMessage } from "@/api/get-error-message";

/** Job failure monitor table for a rolling window (days query param). */
export function useJobMonitorQuery(days?: number) {
  const { data, error, isLoading, isFetching, refetch } = useJobMonitorRetrieve(
    days !== undefined ? { days } : undefined,
    { query: { enabled: days !== undefined } },
  );
  return {
    data: data ?? null,
    error: error ? getErrorMessage(error, "Unable to load job monitor data.") : null,
    loading: isLoading,
    fetching: isFetching,
    refetch,
  };
}
