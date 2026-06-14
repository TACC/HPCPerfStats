"use client";

import { applyBokehResizeObserverDeferral } from "@/patch-resize-observer-for-bokeh";
import LayoutPub from "@/components/LayoutPub";
import { usePubDashboard } from "@/hooks/use-pub-dashboard";
import {
  PubDashboardBundleContext,
} from "@/pub-dashboard-bundle-context";

applyBokehResizeObserverDeferral();

export default function PubLayout({ children }: { children: React.ReactNode }) {
  const { loading, bundle, error } = usePubDashboard();

  return (
    <PubDashboardBundleContext.Provider
      value={{
        loading,
        bundle: bundle as Record<string, unknown> | null,
        error,
      }}
    >
      <LayoutPub machineName={bundle?.machine_name as string | undefined}>
        {children}
      </LayoutPub>
    </PubDashboardBundleContext.Provider>
  );
}
