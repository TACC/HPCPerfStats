"use client";

import Search from "@/views/Search";
import JobList from "@/views/JobList";
import JobDetail from "@/views/JobDetail";
import TypeDetail from "@/views/TypeDetail";
import HostDetail from "@/views/HostDetail";
import AdminMonitor from "@/views/AdminMonitor";
import JobMonitor from "@/views/JobMonitor";
import PageApiKey from "@/views/PageApiKey";
import PageNotFound from "@/views/PageNotFound";

function matchMachineView(slug: string[] | undefined) {
  const parts = slug ?? [];
  if (parts.length === 0) return <Search />;
  if (parts.length === 1) {
    if (parts[0] === "jobs") return <JobList />;
    if (parts[0] === "admin_monitor") return <AdminMonitor />;
    if (parts[0] === "job_monitor") return <JobMonitor />;
    if (parts[0] === "api-key") return <PageApiKey />;
  }
  if (parts.length === 2) {
    const [head] = parts;
    if (head === "job") return <JobDetail />;
    if (head === "year") return <JobList />;
    if (head === "date") return <JobList />;
    if (head === "username") return <JobList />;
    if (head === "account") return <JobList />;
    if (head === "queue") return <JobList />;
    if (head === "host") return <JobList />;
  }
  if (parts.length === 3 && parts[0] === "job") return <TypeDetail />;
  if (parts.length === 3 && parts[0] === "host" && parts[2] === "plot") return <HostDetail />;
  return <PageNotFound />;
}

export default function MachineRouteClient({ slug }: { slug?: string[] }) {
  return matchMachineView(slug);
}
