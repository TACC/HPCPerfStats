import { useJobsFilterOptionsRetrieve } from "@/api/generated/jobs/jobs";
import type { JobsFilterOptionsRetrieveParams } from "@/api/generated/models/jobsFilterOptionsRetrieveParams";
import { getErrorMessage } from "@/api/get-error-message";
import { selectOrvalData } from "@/api/orval-response";
import type { JobListFilterOptions } from "@/components/JobListHeaderFilters";

/** Secondary fetch for job list header filter chips (deferred from GET /api/jobs/). */
export function useJobListFilterOptions(params: Record<string, string>, enabled = true) {
  const { data, error, isLoading, isFetching } = useJobsFilterOptionsRetrieve(
    params as JobsFilterOptionsRetrieveParams,
    { query: { enabled, select: selectOrvalData } },
  );
  return {
    filterOptions: (data?.filter_options ?? null) as JobListFilterOptions | null,
    error: error ? getErrorMessage(error, "Failed to load filter options.") : null,
    optionsLoading: enabled && (isLoading || isFetching) && !data,
  };
}
