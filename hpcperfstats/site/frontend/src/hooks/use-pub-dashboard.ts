import { usePubClusterDashboardRetrieve } from "@/api/generated/public/public";
import { getErrorMessage } from "@/api/get-error-message";

/** Loads anonymous `/api/pub/cluster-dashboard/` bundle for public dashboards. */
export function usePubDashboard() {
  const { data, error, isLoading, refetch } = usePubClusterDashboardRetrieve(undefined, {
    request: { credentials: "omit" },
  });
  return {
    bundle: data ?? null,
    error: error ? getErrorMessage(error, "Unable to load cluster dashboard.") : null,
    loading: isLoading,
    refetch,
  };
}
