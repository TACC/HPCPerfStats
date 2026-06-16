import { useAdminMonitorRetrieve } from "@/api/generated/admin/admin";
import { getErrorMessage } from "@/api/get-error-message";
import type { AdminMonitorSectionResponse } from "@/types/view-models";

export type UseAdminMonitorSectionQueryOptions<T> = {
  section: string;
  enabled: boolean;
  refreshSeq?: number;
  pickResponse: (res: AdminMonitorSectionResponse) => T;
};

/** Lazy-loaded admin monitor section via TanStack Query. */
export function useAdminMonitorSectionQuery<T>({
  section,
  enabled,
  refreshSeq = 0,
  pickResponse,
}: UseAdminMonitorSectionQueryOptions<T>) {
  const { data, error, isLoading, isFetching, refetch } = useAdminMonitorRetrieve(
    {
      section,
      refresh: refreshSeq > 0 ? "1" : undefined,
    },
    {
      query: {
        enabled,
        queryKey: ["adminMonitor", section, refreshSeq],
      },
    },
  );

  const picked = data ? pickResponse(data as unknown as AdminMonitorSectionResponse) : null;
  const initialLoading = enabled && isLoading && picked == null;
  const sectionBusy = enabled && isFetching && !isLoading;

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
