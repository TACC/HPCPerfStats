import { Navigate, Route, Routes } from "react-router-dom";
import PageClusterDashboard from "./pages/PageClusterDashboard.jsx";

export default function AppPub() {
  return (
    <Routes>
      <Route index element={<Navigate to="cluster-dashboard" replace />} />
      <Route path="cluster-dashboard" element={<PageClusterDashboard />} />
      <Route path="*" element={<Navigate to="cluster-dashboard" replace />} />
    </Routes>
  );
}
