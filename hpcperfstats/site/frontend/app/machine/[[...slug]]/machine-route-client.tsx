"use client";

import {
  lazy,
  Suspense,
  useEffect,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
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

/**
 * After the first lazy view resolves, Suspense fallback is null so query-only
 * presentation nav (sort/page/tab) does not flash "Loading page…".
 */
function MachineViewSuspense({ children }: { children: ReactNode }) {
  const [hasResolvedOnce, setHasResolvedOnce] = useState(false);
  return (
    <Suspense
      fallback={
        hasResolvedOnce ? null : <LoadingMessage message="Loading page…" />
      }
    >
      <LazyViewReadyMarker onReady={() => setHasResolvedOnce(true)}>
        {children}
      </LazyViewReadyMarker>
    </Suspense>
  );
}

function LazyViewReadyMarker({
  children,
  onReady,
}: {
  children: ReactNode;
  onReady: () => void;
}) {
  useEffect(() => {
    onReady();
    // Intentionally once per mount of a resolved lazy tree.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mark ready once
  }, []);
  return <>{children}</>;
}

export function matchMachineView(view: MachineRouteView) {
  const View = VIEW_COMPONENTS[view] ?? PageNotFound;
  return (
    <MachineViewSuspense>
      <View />
    </MachineViewSuspense>
  );
}

/** Client pathname/slug is authoritative under static export (nginx serves home shell for deep links). */
export default function MachineRouteClient() {
  const { view } = useMachineRouteParams();
  return matchMachineView(view);
}
