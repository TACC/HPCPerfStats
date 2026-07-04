import { keepPreviousData } from "@tanstack/react-query";
import { useMemo } from "react";
import { useJobsRetrieve } from "@/api/generated/jobs/jobs";
import { getErrorMessage } from "@/api/get-error-message";
import { selectOrvalData } from "@/api/orval-response";
import { buildJobsRetrieveParams } from "@/utils/jobs-retrieve-params";

/** Paginated job list via TanStack Query (histograms loaded separately). */
export function useJobListQuery(params: Record<string, string>) {
  const jobsParams = useMemo(() => buildJobsRetrieveParams(params), [params]);
  const { data, error, isLoading, isFetching, refetch } = useJobsRetrieve(jobsParams, {
    query: { placeholderData: keepPreviousData, select: selectOrvalData },
  });
  return {
    data: data ?? null,
    error: error ? getErrorMessage(error, "Failed to load job list.") : null,
    initialLoading: isLoading && !data,
    tableBusy: isFetching && !isLoading,
    jobsFetching: isFetching,
    refetch,
  };
}
