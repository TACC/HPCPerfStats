import { keepPreviousData } from "@tanstack/react-query";
import { useJobMonitorRetrieve } from "@/api/generated/monitor/monitor";
import { getErrorMessage } from "@/api/get-error-message";
import { selectOrvalData } from "@/api/orval-response";

/** Job failure monitor table for a rolling window (days query param). */
export function useJobMonitorQuery(days?: number) {
  const { data, error, isLoading, isFetching, refetch } = useJobMonitorRetrieve(
    days !== undefined ? { days } : undefined,
    {
      query: {
        enabled: days !== undefined,
        placeholderData: keepPreviousData,
        select: selectOrvalData,
      },
    },
  );
  const initialLoading = isLoading && !data;
  const tableBusy = isFetching && !isLoading;
  return {
    data: data ?? null,
    error: error ? getErrorMessage(error, "Unable to load job monitor data.") : null,
    initialLoading,
    tableBusy,
    /** @deprecated Prefer initialLoading / tableBusy per interactive-ready-controls.mdc */
    loading: initialLoading,
    fetching: isFetching,
    refetch,
  };
}
