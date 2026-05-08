import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { fetchPubClusterDashboard } from "./api.js";
import LayoutPub from "./components/LayoutPub.jsx";
import PageClusterDashboard from "./pages/PageClusterDashboard.jsx";
import { PubDashboardBundleContext } from "./pub-dashboard-bundle-context.js";

export default function AppPub() {
  const [state, setState] = useState(() => ({
    loading: true,
    bundle: null,
    error: null,
  }));

  useEffect(() => {
    let cancelled = false;
    fetchPubClusterDashboard()
      .then((bundle) => {
        if (!cancelled) setState({ loading: false, bundle, error: null });
      })
      .catch((err) => {
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
      <LayoutPub machineName={state.bundle?.machine_name}>
        <Routes>
          <Route index element={<Navigate to="cluster-dashboard" replace />} />
          <Route path="cluster-dashboard" element={<PageClusterDashboard />} />
          <Route path="*" element={<Navigate to="cluster-dashboard" replace />} />
        </Routes>
      </LayoutPub>
    </PubDashboardBundleContext.Provider>
  );
}
