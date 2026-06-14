import { useJobsRetrieve3 } from "@/api/generated/jobs/jobs";
import { getErrorMessage } from "@/api/get-error-message";
import type { TypeDetailData } from "@/types/view-models";

/** Type detail page payload (`GET /api/jobs/{jid}/{type_name}/`). */
export function useTypeDetailQuery(jid: string, typeName: string) {
  const enabled = !!jid && !!typeName;
  const { data, error, isLoading } = useJobsRetrieve3(jid, typeName, {
    query: { enabled },
  });
  return {
    data: (data ?? null) as TypeDetailData | null,
    error: error ? getErrorMessage(error, "Request failed") : null,
    loading: enabled && isLoading,
  };
}
