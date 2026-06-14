import { useQueryClient } from "@tanstack/react-query";
import {
  getUserApiKeyRetrieveQueryKey,
  useUserApiKeyRetrieve,
  useUserApiKeyRotateCreate,
} from "@/api/generated/session/session";
import { getErrorMessage } from "@/api/get-error-message";

/** API key status + rotate mutation with query invalidation. */
export function useUserApiKey() {
  const queryClient = useQueryClient();
  const { data, error, isLoading, refetch } = useUserApiKeyRetrieve();
  const rotateMutation = useUserApiKeyRotateCreate({
    mutation: {
      onSuccess: (rotated) => {
        queryClient.setQueryData(getUserApiKeyRetrieveQueryKey(), rotated);
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
