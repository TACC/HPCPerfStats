"use client";

import { useEffect } from "react";
import { applyBokehResizeObserverDeferral } from "@/patch-resize-observer-for-bokeh";
import { useSessionRetrieve } from "@/api/generated/session/session";
import Layout from "@/Layout";
import LoadingMessage from "@/components/LoadingMessage";
import { SessionContext, type SessionData } from "@/session-context";
import { useDocumentTitle } from "@/utils/useDocumentTitle";

applyBokehResizeObserverDeferral();

function SessionGateLayout({ message, title }: { message: string; title: string }) {
  useDocumentTitle(title);
  return (
    <>
      <a href="#main-content" className="visually-hidden visually-hidden-focusable">
        Skip to main content
      </a>
      <main id="main-content" tabIndex={-1}>
        <LoadingMessage message={message} />
      </main>
    </>
  );
}

export default function MachineLayout({ children }: { children: React.ReactNode }) {
  const { data: sessionData, isLoading, isError } = useSessionRetrieve();

  const session: SessionData | null = sessionData
    ? {
        logged_in: sessionData.logged_in,
        username: sessionData.username,
        is_staff: sessionData.is_staff,
        machine_name: sessionData.machine_name,
      }
    : isError
      ? null
      : null;

  useEffect(() => {
    if (isLoading) return;
    if (session?.logged_in) return;
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    const target = next ? `/login_prompt?next=${next}` : "/login_prompt";
    window.location.replace(target);
  }, [isLoading, session]);

  if (isLoading) {
    return <SessionGateLayout message="Loading session…" title="Loading session" />;
  }

  if (!session?.logged_in) {
    return <SessionGateLayout message="Redirecting to sign in…" title="Redirecting to sign in" />;
  }

  return (
    <SessionContext.Provider value={session}>
      <Layout session={session} onSessionChange={() => {}}>
        {children}
      </Layout>
    </SessionContext.Provider>
  );
}
