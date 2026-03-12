import { useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { api } from "./api";
import Layout from "./Layout";
import LoadingMessage from "./components/LoadingMessage";
import Search from "./pages/Search";
import JobList from "./pages/JobList";
import JobDetail from "./pages/JobDetail";
import TypeDetail from "./pages/TypeDetail";
import HostDetail from "./pages/HostDetail";
import AdminMonitor from "./pages/AdminMonitor";
import JobMonitor from "./pages/JobMonitor";

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getSession()
      .then(setSession)
      .catch(() => setSession(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <LoadingMessage message="Loading session…" />;
  }

  if (!session || !session.logged_in) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = next ? `/login_prompt?next=${next}` : "/login_prompt";
    return null;
  }

  return (
    <Layout session={session}>
      <Routes>
        <Route index element={<Search />} />
        <Route path="job/:pk" element={<JobDetail />} />
        <Route path="year/:year" element={<JobList />} />
        <Route path="date/:date" element={<JobList />} />
        <Route path="username/:username" element={<JobList />} />
        <Route path="account/:account" element={<JobList />} />
        <Route path="queue/:queue" element={<JobList />} />
        <Route path="host/:host" element={<JobList />} />
        <Route path="jobs" element={<JobList />} />
        <Route path="job/:jid/:typeName" element={<TypeDetail />} />
        <Route path="host/:host/plot" element={<HostDetail />} />
        <Route path="admin_monitor" element={<AdminMonitor />} />
        <Route path="job_monitor" element={<JobMonitor />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
