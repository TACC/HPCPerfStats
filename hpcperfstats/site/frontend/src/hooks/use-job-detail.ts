import { useJobsRetrieve2 } from "@/api/generated/jobs/jobs";
import type { JobDetailResponse } from "@/api/generated/models/jobDetailResponse";
import { getErrorMessage, getStatusAwareErrorMessage } from "@/api/get-error-message";

/** Light-then-full job detail via sequential Orval queries. */
export function useJobDetailQuery(pk: string) {
  const lightQuery = useJobsRetrieve2(
    pk,
    { light: 1 },
    { query: { enabled: !!pk } },
  );
  const fullQuery = useJobsRetrieve2(pk, undefined, {
    query: { enabled: !!pk && !!lightQuery.data && !lightQuery.isError },
  });

  const data = (fullQuery.data ?? lightQuery.data ?? null) as JobDetailResponse | null;
  const error = lightQuery.error ?? fullQuery.error;
  const loading = lightQuery.isLoading;
  const detailsLoading = fullQuery.isFetching && !fullQuery.data && !fullQuery.isError;
  const detailFetchWarning = fullQuery.isError && !lightQuery.isError;

  return {
    data,
    error: error
      ? getStatusAwareErrorMessage(error, getErrorMessage(error, "Request failed"))
      : null,
    loading,
    detailsLoading,
    detailFetchWarning,
    refetchLight: lightQuery.refetch,
    refetchFull: fullQuery.refetch,
  };
}
