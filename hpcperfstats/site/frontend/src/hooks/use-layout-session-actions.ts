import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  getHomeRetrieveQueryKey,
} from "@/api/generated/home/home";
import {
  getSessionRetrieveQueryKey,
  getSessionRetrieveQueryOptions,
  useSessionDropStaffCreate,
} from "@/api/generated/session/session";
import { useCacheInvalidatePageCreate } from "@/api/generated/admin/admin";
import { getApiBody, getErrorMessage } from "@/api/get-error-message";
import { orvalResponseData } from "@/api/orval-response";
import type { SessionInfo } from "@/api/generated/models/sessionInfo";
import type { InvalidateCacheResponse } from "@/api/generated/models/invalidateCacheResponse";
import type { SessionData } from "@/session-context";
import { SITE_MACHINE_NAME } from "@/config/site-identity";

type UseLayoutSessionActionsArgs = {
  pathname: string;
  onSessionChange?: (nextSession: SessionData | null) => void;
};

/** Staff session actions (drop staff, invalidate page cache) for Layout chrome. */
export function useLayoutSessionActions({
  pathname,
  onSessionChange,
}: UseLayoutSessionActionsArgs) {
  const queryClient = useQueryClient();
  const dropStaffMutation = useSessionDropStaffCreate();
  const invalidateCacheMutation = useCacheInvalidatePageCreate();
  const [staffMessage, setStaffMessage] = useState("");
  const [isDroppingStaff, setIsDroppingStaff] = useState(false);
  const [isInvalidatingCache, setIsInvalidatingCache] = useState(false);

  const handleDropStaffForSession = useCallback(
    async (closeMenu?: () => void) => {
      if (isDroppingStaff) return;
      if (
        !window.confirm(
          "Remove staff permissions for this browser session? You can restore them by signing out and signing in again.",
        )
      ) {
        return;
      }
      setIsDroppingStaff(true);
      setStaffMessage("");
      try {
        const response = await dropStaffMutation.mutateAsync();
        await queryClient.invalidateQueries({ queryKey: getSessionRetrieveQueryKey() });
        const refreshedSession = await queryClient.fetchQuery(getSessionRetrieveQueryOptions());
        if (typeof onSessionChange === "function") {
          const sessionBody = orvalResponseData<SessionInfo>(refreshedSession);
          onSessionChange(
            sessionBody
              ? {
                  logged_in: sessionBody.logged_in,
                  username: sessionBody.username,
                  is_staff: sessionBody.is_staff,
                  machine_name: sessionBody.machine_name ?? SITE_MACHINE_NAME,
                }
              : null,
          );
        }
        const responseBody = getApiBody(response);
        setStaffMessage(
          responseBody.message ||
            "Staff access removed for this session. Log out and log back in to restore staff access.",
        );
      } catch (error: unknown) {
        setStaffMessage(
          getErrorMessage(error, "Unable to remove staff access for this session."),
        );
      } finally {
        setIsDroppingStaff(false);
        closeMenu?.();
      }
    },
    [dropStaffMutation, isDroppingStaff, onSessionChange, queryClient],
  );

  const handleInvalidateCacheForPage = useCallback(
    async (closeMenu?: () => void) => {
      if (isInvalidatingCache) return;
      const pagePathForCache =
        typeof window !== "undefined" && window.location.pathname
          ? window.location.pathname
          : pathname;
      if (
        !window.confirm(
          `Invalidate cached data for the current page path (${pagePathForCache})?`,
        )
      ) {
        return;
      }
      setIsInvalidatingCache(true);
      setStaffMessage("");
      try {
        const response = await invalidateCacheMutation.mutateAsync({
          data: { page_path: pagePathForCache },
        });
        const deletedCount = Number(
          orvalResponseData<InvalidateCacheResponse>(response)?.deleted_keys || 0,
        );
        setStaffMessage(
          `Invalidated ${deletedCount} cache key${deletedCount === 1 ? "" : "s"} for ${pagePathForCache}.`,
        );
        void queryClient.invalidateQueries({ queryKey: getHomeRetrieveQueryKey() });
      } catch (error: unknown) {
        setStaffMessage(getErrorMessage(error, "Unable to invalidate cache for this page."));
      } finally {
        setIsInvalidatingCache(false);
        closeMenu?.();
      }
    },
    [invalidateCacheMutation, isInvalidatingCache, pathname, queryClient],
  );

  return {
    staffMessage,
    setStaffMessage,
    isDroppingStaff,
    isInvalidatingCache,
    staffMenuBusy: isDroppingStaff || isInvalidatingCache,
    handleDropStaffForSession,
    handleInvalidateCacheForPage,
  };
}
