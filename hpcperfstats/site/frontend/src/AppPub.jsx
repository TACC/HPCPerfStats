import { Navigate, Route, Routes } from "react-router-dom";
import PageMonthlyMetrics from "./pages/PageMonthlyMetrics.jsx";

export default function AppPub() {
  return (
    <Routes>
      <Route index element={<Navigate to="monthly-metrics" replace />} />
      <Route path="monthly-metrics" element={<PageMonthlyMetrics />} />
      <Route path="*" element={<Navigate to="monthly-metrics" replace />} />
    </Routes>
  );
}
