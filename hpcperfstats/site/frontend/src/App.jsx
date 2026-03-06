import { useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { api } from "./api";
import Layout from "./Layout";
import Search from "./pages/Search";
import JobList from "./pages/JobList";
import JobDetail from "./pages/JobDetail";
import TypeDetail from "./pages/TypeDetail";
import AdminMonitor from "./pages/AdminMonitor";

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
    return (
      <div className="container-fluid text-center" style={{ padding: "2rem" }}>
        Loading…
      </div>
    );
  }

  if (!session || !session.logged_in) {
    window.location.href = "/login_prompt";
    return null;
  }

  return (
    <Layout session={session}>
      <Routes>
        <Route index element={<Search />} />
        <Route path="job/:pk" element={<JobDetail />} />
        <Route path="date/:date" element={<JobList />} />
        <Route path="username/:username" element={<JobList />} />
        <Route path="account/:account" element={<JobList />} />
        <Route path="host/:host" element={<JobList />} />
        <Route path="jobs" element={<JobList />} />
        <Route path="job/:jid/:typeName" element={<TypeDetail />} />
        <Route path="admin_monitor" element={<AdminMonitor />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
