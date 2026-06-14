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
import { useMachineRouteParams } from "@/hooks/use-machine-route-params";
import type { MachineRouteView } from "@/utils/machine-route-params";

export function matchMachineView(view: MachineRouteView) {
  switch (view) {
    case "search":
      return <Search />;
    case "jobList":
      return <JobList />;
    case "jobDetail":
      return <JobDetail />;
    case "typeDetail":
      return <TypeDetail />;
    case "hostDetail":
      return <HostDetail />;
    case "adminMonitor":
      return <AdminMonitor />;
    case "jobMonitor":
      return <JobMonitor />;
    case "pageApiKey":
      return <PageApiKey />;
    default:
      return <PageNotFound />;
  }
}

/** Client pathname/slug is authoritative under static export (nginx serves home shell for deep links). */
export default function MachineRouteClient() {
  const { view } = useMachineRouteParams();
  return matchMachineView(view);
}
