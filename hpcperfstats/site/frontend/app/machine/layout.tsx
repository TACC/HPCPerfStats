"use client";

import { useCallback, useEffect, Suspense } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSessionRetrieveQueryKey } from "@/api/generated/session/session";
import type { SessionInfo } from "@/api/generated/models/sessionInfo";
import { applyBokehResizeObserverDeferral } from "@/patch-resize-observer-for-bokeh";
import { useSessionRetrieve } from "@/api/generated/session/session";
import Layout from "@/Layout";
import LoadingMessage from "@/components/LoadingMessage";
import { SessionContext, type SessionData } from "@/session-context";
import { useDocumentTitle } from "@/utils/useDocumentTitle";
import { SITE_MACHINE_NAME } from "@/config/site-identity";

applyBokehResizeObserverDeferral();

const PLACEHOLDER_SESSION: SessionData = {
  logged_in: true,
  username: "",
  is_staff: false,
  machine_name: SITE_MACHINE_NAME,
};

function sessionFromApi(data: SessionInfo): SessionData {
  return {
    logged_in: data.logged_in,
    username: data.username,
    is_staff: data.is_staff,
    machine_name: data.machine_name ?? SITE_MACHINE_NAME,
  };
}

export default function MachineLayout({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const { data: sessionData, isLoading, isError } = useSessionRetrieve();

  const session: SessionData | null = sessionData
    ? sessionFromApi(sessionData)
    : isError
      ? null
      : null;

  const handleSessionChange = useCallback(
    (next: SessionData | null) => {
      if (next) {
        queryClient.setQueryData(getSessionRetrieveQueryKey(), next);
      } else {
        void queryClient.invalidateQueries({ queryKey: getSessionRetrieveQueryKey() });
      }
    },
    [queryClient],
  );

  useEffect(() => {
    if (isLoading) return;
    if (session?.logged_in) return;
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    const target = next ? `/login_prompt?next=${next}` : "/login_prompt";
    window.location.replace(target);
  }, [isLoading, session]);

  const loadingTitle = SITE_MACHINE_NAME.trim() || "HPCPerfStats";
  useDocumentTitle(
    isLoading ? loadingTitle : !session?.logged_in ? "Redirecting to sign in" : " ",
  );

  if (!isLoading && !session?.logged_in) {
    return (
      <main id="main-content" tabIndex={-1}>
        <LoadingMessage message="Redirecting to sign in…" />
      </main>
    );
  }

  const layoutSession = session ?? PLACEHOLDER_SESSION;

  return (
    <SessionContext.Provider value={layoutSession}>
      <Layout session={layoutSession} onSessionChange={handleSessionChange}>
        {isLoading ? (
          <span className="sr-only" role="status" aria-live="polite">
            Loading session…
          </span>
        ) : null}
        <Suspense
          fallback={
            <span className="sr-only" role="status" aria-live="polite">
              Loading page…
            </span>
          }
        >
          {children}
        </Suspense>
      </Layout>
    </SessionContext.Provider>
  );
}
