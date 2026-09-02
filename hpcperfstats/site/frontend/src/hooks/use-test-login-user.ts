import { useQueryClient } from "@tanstack/react-query";
import {
  getTestLoginUserRetrieveQueryKey,
  useTestLoginUserCreate,
  useTestLoginUserRetrieve,
} from "@/api/generated/session/session";
import { getErrorMessage } from "@/api/get-error-message";
import { orvalResponseData, selectOrvalData } from "@/api/orval-response";

/** Staff GET/POST for the singleton development test-login user. */
export function useTestLoginUser() {
  const queryClient = useQueryClient();
  const { data, error, isLoading, refetch } = useTestLoginUserRetrieve({
    query: { select: selectOrvalData, retry: false },
  });
  const saveMutation = useTestLoginUserCreate({
    mutation: {
      onSuccess: (saved) => {
        const body = orvalResponseData(saved);
        if (body) {
          queryClient.setQueryData(getTestLoginUserRetrieveQueryKey(), {
            status: 200,
            data: body,
            headers: saved.headers,
          });
        }
      },
    },
  });

  return {
    data: data ?? null,
    error: error ? getErrorMessage(error, "Unable to load test user.") : null,
    loading: isLoading,
    refetch,
    save: (username: string, password: string) =>
      saveMutation.mutateAsync({ data: { username, password } }),
    saving: saveMutation.isPending,
    saveError: saveMutation.error
      ? getErrorMessage(saveMutation.error, "Unable to save test user.")
      : null,
  };
}
