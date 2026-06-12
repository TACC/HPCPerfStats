import type { AdminMonitorSectionResponse } from "@/types/view-models";

export type AdminMonitorSectionLoaderOptions<T> = {
  section: string;
  pickResponse: (res: AdminMonitorSectionResponse) => T;
  setLoading: (loading: boolean) => void;
  setError: (message: string | null) => void;
  setData: (data: T) => void;
  apiClient: {
    getAdminMonitorSection: (
      section: string,
      options?: { refresh?: boolean },
    ) => Promise<unknown>;
  };
};

/** Factory for admin_monitor section loaders (shared request / loading / error handling). */
export function createAdminMonitorSectionLoader<T>(
  opts: AdminMonitorSectionLoaderOptions<T>,
) {
  const { section, pickResponse, setLoading, setError, setData, apiClient } = opts;
  return function loadAdminMonitorSection(forceRefresh = false) {
    setLoading(true);
    setError(null);
    return apiClient
      .getAdminMonitorSection(section, { refresh: forceRefresh })
      .then((res) => setData(pickResponse(res as AdminMonitorSectionResponse)))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Request failed"),
      )
      .finally(() => setLoading(false));
  };
}
