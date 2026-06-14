import type { AdminMonitorSectionResponse } from "@/types/view-models";
import { adminMonitorRetrieve } from "@/api/generated/admin/admin";
import { getErrorMessage } from "@/api/get-error-message";

export type AdminMonitorSectionLoaderOptions<T> = {
  section: string;
  pickResponse: (res: AdminMonitorSectionResponse) => T;
  setLoading: (loading: boolean) => void;
  setError: (message: string | null) => void;
  setData: (data: T) => void;
};

/** Factory for admin_monitor section loaders (shared request / loading / error handling). */
export function createAdminMonitorSectionLoader<T>(
  opts: AdminMonitorSectionLoaderOptions<T>,
) {
  const { section, pickResponse, setLoading, setError, setData } = opts;
  return function loadAdminMonitorSection(forceRefresh = false) {
    setLoading(true);
    setError(null);
    return adminMonitorRetrieve({
      section,
      refresh: forceRefresh ? "1" : undefined,
    })
      .then((res) =>
        setData(pickResponse(res as unknown as AdminMonitorSectionResponse)),
      )
      .catch((e: unknown) =>
        setError(getErrorMessage(e, "Request failed")),
      )
      .finally(() => setLoading(false));
  };
}
