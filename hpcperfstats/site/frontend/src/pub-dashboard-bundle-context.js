import { createContext, useContext } from "react";

export const PubDashboardBundleContext = createContext(null);

export function usePubDashboardBundle() {
  const ctx = useContext(PubDashboardBundleContext);
  if (ctx === null) {
    throw new Error("usePubDashboardBundle requires PubDashboardBundleContext.Provider");
  }
  return ctx;
}
