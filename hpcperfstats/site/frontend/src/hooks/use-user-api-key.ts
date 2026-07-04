import { useQueryClient } from "@tanstack/react-query";
import {
  getUserApiKeyRetrieveQueryKey,
  useUserApiKeyRetrieve,
  useUserApiKeyRotateCreate,
} from "@/api/generated/session/session";
import { getErrorMessage } from "@/api/get-error-message";
import { orvalResponseData, selectOrvalData } from "@/api/orval-response";

/** API key status + rotate mutation with query invalidation. */
export function useUserApiKey() {
  const queryClient = useQueryClient();
  const { data, error, isLoading, refetch } = useUserApiKeyRetrieve({
    query: { select: selectOrvalData },
  });
  const rotateMutation = useUserApiKeyRotateCreate({
    mutation: {
      onSuccess: (rotated) => {
        const body = orvalResponseData(rotated);
        if (body) {
          queryClient.setQueryData(getUserApiKeyRetrieveQueryKey(), {
            status: 200,
            data: body,
            headers: rotated.headers,
          });
        }
      },
    },
  });

  return {
    data: data ?? null,
    error: error ? getErrorMessage(error, "Unable to load API key status.") : null,
    loading: isLoading,
    refetch,
    rotate: rotateMutation.mutateAsync,
    rotating: rotateMutation.isPending,
    rotateError: rotateMutation.error
      ? getErrorMessage(rotateMutation.error, "Unable to rotate API key.")
      : null,
  };
}
