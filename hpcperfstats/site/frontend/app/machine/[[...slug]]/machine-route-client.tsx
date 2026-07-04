"use client";

import { lazy, Suspense, type ComponentType } from "react";
import LoadingMessage from "@/components/LoadingMessage";
import { useMachineRouteParams } from "@/hooks/use-machine-route-params";
import type { MachineRouteView } from "@/utils/machine-route-params";

const Search = lazy(() => import("@/views/Search"));
const JobList = lazy(() => import("@/views/JobList"));
const JobDetail = lazy(() => import("@/views/JobDetail"));
const TypeDetail = lazy(() => import("@/views/TypeDetail"));
const HostDetail = lazy(() => import("@/views/HostDetail"));
const AdminMonitor = lazy(() => import("@/views/AdminMonitor"));
const JobMonitor = lazy(() => import("@/views/JobMonitor"));
const PageApiKey = lazy(() => import("@/views/PageApiKey"));
const PageNotFound = lazy(() => import("@/views/PageNotFound"));

const VIEW_COMPONENTS: Record<MachineRouteView, ComponentType> = {
  search: Search,
  jobList: JobList,
  jobDetail: JobDetail,
  typeDetail: TypeDetail,
  hostDetail: HostDetail,
  adminMonitor: AdminMonitor,
  jobMonitor: JobMonitor,
  pageApiKey: PageApiKey,
  notFound: PageNotFound,
};

export function matchMachineView(view: MachineRouteView) {
  const View = VIEW_COMPONENTS[view] ?? PageNotFound;
  return (
    <Suspense fallback={<LoadingMessage message="Loading page…" />}>
      <View />
    </Suspense>
  );
}

/** Client pathname/slug is authoritative under static export (nginx serves home shell for deep links). */
export default function MachineRouteClient() {
  const { view } = useMachineRouteParams();
  return matchMachineView(view);
}
