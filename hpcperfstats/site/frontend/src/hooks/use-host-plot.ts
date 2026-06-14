import { useHostPlotRetrieve } from "@/api/generated/hosts/hosts";
import type { HostPlotRetrieveParams } from "@/api/generated/models/hostPlotRetrieveParams";
import { getErrorMessage } from "@/api/get-error-message";
import type { HostDetailData } from "@/types/view-models";

/** Host utilization plot for a host + time range (`GET /api/host_plot/`). */
export function useHostPlotQuery(params: HostPlotRetrieveParams | null) {
  const enabled = !!params?.host && !!params?.end_time__gte;
  const { data, error, isLoading } = useHostPlotRetrieve(params ?? { host: "", end_time__gte: "" }, {
    query: { enabled },
  });
  return {
    data: (data ?? null) as HostDetailData | null,
    error: error ? getErrorMessage(error, "Request failed") : null,
    loading: enabled && isLoading,
  };
}
