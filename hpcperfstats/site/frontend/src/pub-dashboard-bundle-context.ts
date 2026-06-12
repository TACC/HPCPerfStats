import { createContext, useContext } from "react";

export type PubDashboardBundleState = {
  loading: boolean;
  bundle: Record<string, unknown> | null;
  error: string | null;
};

export const PubDashboardBundleContext = createContext<PubDashboardBundleState | null>(null);

export function usePubDashboardBundle(): PubDashboardBundleState {
  const ctx = useContext(PubDashboardBundleContext);
  if (ctx === null) {
    throw new Error("usePubDashboardBundle requires PubDashboardBundleContext.Provider");
  }
  return ctx;
}
