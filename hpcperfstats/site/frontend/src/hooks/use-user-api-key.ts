import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  getUserApiKeyRetrieveQueryKey,
  useUserApiKeyRetrieve,
  useUserApiKeyRotateCreate,
} from "@/api/generated/session/session";
import { getErrorMessage } from "@/api/get-error-message";
import { orvalResponseData, selectOrvalData } from "@/api/orval-response";

/**
 * API key status + rotate mutation.
 *
 * The server returns ``raw_key`` exactly once (on first GET that mints a key, or
 * on rotate); every later GET returns ``raw_key: null``. The revealed key is
 * therefore latched in local state so a refetch (explicit, window focus, or
 * remount of the query) cannot erase it before the user copies it.
 */
export function useUserApiKey() {
  const queryClient = useQueryClient();
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const { data, error, isLoading, refetch } = useUserApiKeyRetrieve({
    query: { select: selectOrvalData },
  });
  const fetchedRawKey = data?.raw_key ?? null;
  useEffect(() => {
    if (fetchedRawKey) setRevealedKey(fetchedRawKey);
  }, [fetchedRawKey]);
  const rotateMutation = useUserApiKeyRotateCreate({
    mutation: {
      onSuccess: (rotated) => {
        const body = orvalResponseData<{ raw_key?: string | null }>(rotated);
        if (body?.raw_key) setRevealedKey(body.raw_key);
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

  const clearRawKeyFromCache = () => {
    setRevealedKey(null);
    queryClient.setQueryData(getUserApiKeyRetrieveQueryKey(), (current) => {
      if (!current || typeof current !== "object" || !("data" in current)) return current;
      const envelope = current as { status: number; data: Record<string, unknown>; headers: Headers };
      const nextData = envelope.data;
      if (!nextData || nextData.raw_key == null) return current;
      return {
        ...envelope,
        data: { ...nextData, raw_key: null },
      };
    });
  };

  return {
    data: data ? { ...data, raw_key: fetchedRawKey ?? revealedKey } : null,
    error: error ? getErrorMessage(error, "Unable to load API key status.") : null,
    loading: isLoading,
    refetch,
    rotate: rotateMutation.mutateAsync,
    rotating: rotateMutation.isPending,
    rotateError: rotateMutation.error
      ? getErrorMessage(rotateMutation.error, "Unable to rotate API key.")
      : null,
    clearRawKeyFromCache,
  };
}
