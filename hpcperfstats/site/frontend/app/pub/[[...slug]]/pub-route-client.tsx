"use client";

import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import LoadingMessage from "@/components/LoadingMessage";
import PageClusterDashboard from "@/views/PageClusterDashboard";

export default function PubRouteClient({ slug }: { slug?: string[] }) {
  const router = useRouter();
  const parts = useMemo(() => slug ?? [], [slug]);

  useEffect(() => {
    if (parts.length === 0) {
      router.replace("/pub/cluster-dashboard/");
    } else if (parts.length !== 1 || parts[0] !== "cluster-dashboard") {
      router.replace("/pub/cluster-dashboard/");
    }
  }, [parts, router]);

  if (parts.length === 1 && parts[0] === "cluster-dashboard") {
    return <PageClusterDashboard />;
  }

  return <LoadingMessage message="Loading dashboard…" />;
}
