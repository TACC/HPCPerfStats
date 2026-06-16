import { keepPreviousData } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { useJobsRetrieve2 } from "@/api/generated/jobs/jobs";
import type { JobDetailResponse } from "@/api/generated/models/jobDetailResponse";
import { getErrorMessage, getStatusAwareErrorMessage } from "@/api/get-error-message";

const INITIAL_DEFER = "xalt,proc,multiprecision";

/** Single job detail fetch with deferred heavy sections; refetch without defer on demand. */
export function useJobDetailQuery(pk: string) {
  const [deferParam, setDeferParam] = useState(INITIAL_DEFER);
  const detailQuery = useJobsRetrieve2(
    pk,
    deferParam ? { defer: deferParam } : undefined,
    {
      query: {
        enabled: !!pk,
        placeholderData: keepPreviousData,
      },
    },
  );

  useEffect(() => {
    setDeferParam(INITIAL_DEFER);
  }, [pk]);

  const data = (detailQuery.data ?? null) as JobDetailResponse | null;
  const error = detailQuery.error;
  const initialLoading = detailQuery.isLoading && !detailQuery.data;
  const detailBusy = detailQuery.isFetching && !detailQuery.isLoading && !!detailQuery.data;
  const detailsLoading =
    detailQuery.isFetching &&
    !detailQuery.isError &&
    !!detailQuery.data &&
    deferParam !== INITIAL_DEFER;
  const detailFetchWarning = detailQuery.isError;

  const loadFullDetail = useCallback(() => {
    setDeferParam("");
  }, []);

  const loadDetailWithoutDeferParts = useCallback((parts: string[]) => {
    setDeferParam(parts.join(","));
  }, []);

  return {
    data,
    error: error
      ? getStatusAwareErrorMessage(error, getErrorMessage(error, "Request failed"))
      : null,
    initialLoading,
    detailBusy,
    detailsLoading,
    detailFetchWarning,
    deferParam,
    loadFullDetail,
    loadDetailWithoutDeferParts,
    refetchDetail: detailQuery.refetch,
  };
}
