import { useAdminMonitorRetrieve } from "@/api/generated/admin/admin";
import { getErrorMessage } from "@/api/get-error-message";
import { selectOrvalData } from "@/api/orval-response";
import type { AdminMonitorSectionResponse } from "@/types/view-models";
import { keepPreviousData } from "@tanstack/react-query";

export type UseAdminMonitorSectionQueryOptions<T> = {
  section: string;
  enabled: boolean;
  refreshSeq?: number;
  /** ``group_by`` query value (``section=ingest_coverage`` only). */
  groupBy?: string;
  pickResponse: (res: AdminMonitorSectionResponse) => T;
};

/** Lazy-loaded admin monitor section via TanStack Query. */
export function useAdminMonitorSectionQuery<T>({
  section,
  enabled,
  refreshSeq = 0,
  groupBy,
  pickResponse,
}: UseAdminMonitorSectionQueryOptions<T>) {
  const { data, error, isLoading, isFetching, refetch } = useAdminMonitorRetrieve(
    {
      section,
      refresh: refreshSeq > 0 ? "1" : undefined,
      ...(groupBy ? { group_by: groupBy } : {}),
    },
    {
      query: {
        enabled,
        queryKey: groupBy
          ? ["adminMonitor", section, refreshSeq, groupBy]
          : ["adminMonitor", section, refreshSeq],
        select: selectOrvalData,
        placeholderData: keepPreviousData,
      },
    },
  );

  const picked = data ? pickResponse(data as unknown as AdminMonitorSectionResponse) : null;
  const initialLoading = enabled && isLoading && picked == null;
  const sectionBusy = enabled && isFetching && !isLoading && picked != null;

  return {
    data: picked,
    error: error ? getErrorMessage(error, "Request failed") : null,
    initialLoading,
    sectionBusy,
    /** @deprecated Prefer initialLoading / sectionBusy per interactive-ready-controls.mdc */
    loading: initialLoading || sectionBusy,
    refetch,
  };
}
