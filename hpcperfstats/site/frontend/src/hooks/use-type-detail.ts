import { useJobsRetrieve3 } from "@/api/generated/jobs/jobs";
import { getErrorMessage } from "@/api/get-error-message";
import { selectOrvalData } from "@/api/orval-response";
import type { TypeDetailData } from "@/types/view-models";
import { keepPreviousData } from "@tanstack/react-query";

/** Type detail page payload (`GET /api/jobs/{jid}/{type_name}/`). */
export function useTypeDetailQuery(jid: string, typeName: string) {
  const enabled = !!jid && !!typeName;
  const { data, error, isLoading, isFetching } = useJobsRetrieve3(jid, typeName, {
    query: {
      enabled,
      select: selectOrvalData,
      placeholderData: keepPreviousData,
    },
  });
  return {
    data: (data ?? null) as TypeDetailData | null,
    error: error ? getErrorMessage(error, "Request failed") : null,
    loading: enabled && isLoading && !data,
    detailBusy: enabled && isFetching && !isLoading && !!data,
  };
}
