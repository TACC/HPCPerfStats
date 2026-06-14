import { keepPreviousData } from "@tanstack/react-query";
import { useJobsRetrieve } from "@/api/generated/jobs/jobs";
import type { JobsRetrieveParams } from "@/api/generated/models/jobsRetrieveParams";
import { getErrorMessage } from "@/api/get-error-message";

/** Paginated job list via TanStack Query (histograms loaded separately). */
export function useJobListQuery(params: Record<string, string>) {
  const { data, error, isLoading, isFetching, refetch } = useJobsRetrieve(
    params as JobsRetrieveParams,
    { query: { placeholderData: keepPreviousData } },
  );
  return {
    data: data ?? null,
    error: error ? getErrorMessage(error, "Failed to load job list.") : null,
    initialLoading: isLoading && !data,
    tableBusy: isFetching && !isLoading,
    refetch,
  };
}
