"use client";

import { useEffect, useMemo, useState } from "react";
import { applyBokehResizeObserverDeferral } from "@/patch-resize-observer-for-bokeh";
import { fetchPubClusterDashboard } from "@/api/fetch-mutator";
import LayoutPub from "@/components/LayoutPub";
import {
  PubDashboardBundleContext,
  type PubDashboardBundleState,
} from "@/pub-dashboard-bundle-context";

applyBokehResizeObserverDeferral();

export default function PubLayout({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<PubDashboardBundleState>({
    loading: true,
    bundle: null,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    fetchPubClusterDashboard()
      .then((bundle) => {
        if (!cancelled) setState({ loading: false, bundle: bundle as Record<string, unknown>, error: null });
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setState({
            loading: false,
            bundle: null,
            error: err?.message || String(err),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const contextValue = useMemo(
    () => ({
      loading: state.loading,
      bundle: state.bundle,
      error: state.error,
    }),
    [state.loading, state.bundle, state.error],
  );

  return (
    <PubDashboardBundleContext.Provider value={contextValue}>
      <LayoutPub machineName={state.bundle?.machine_name as string | undefined}>
        {children}
      </LayoutPub>
    </PubDashboardBundleContext.Provider>
  );
}
