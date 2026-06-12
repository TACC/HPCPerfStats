import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { BokehJsonItem } from "@/types/bokeh";
import type { HostDetailData } from "@/types/view-models";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import { api } from "@/api";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehPlotWithLimitation from "../components/BokehPlotWithLimitation";
import LoadingMessage from "../components/LoadingMessage";
import { formatDateTime } from "../utils/formatDateTime";
import { buildAsyncPageTitle } from "../utils/async-page-title";
import { useDocumentTitle } from "../utils/useDocumentTitle";

function routeParamString(value: string | string[] | undefined): string {
  if (Array.isArray(value)) return value[0] ?? "";
  return value ?? "";
}

export default function HostDetail() {
  const params = useParams();
  const host = routeParamString(params.host);
  const searchParams = useSearchParams();
  const [data, setData] = useState<HostDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useDocumentTitle(
    buildAsyncPageTitle({
      loading: loading && !!host,
      hasError: !!error,
      loadingTitle: `Loading host ${host || ""}`.trim(),
      readyTitle:
        host && typeof data?.host === "string" ? `Host ${data.host} · plot` : "",
      fallbackTitle: host ? `Host ${host}` : "Host plot",
    }),
  );

  useEffect(() => {
    if (!host) return;
    let end_time__gte = searchParams.get("end_time__gte") || "";
    const end_time__lte = searchParams.get("end_time__lte") || "now()";
    if (!end_time__gte) {
      const d = new Date();
      d.setDate(d.getDate() - 1);
      end_time__gte = d.toISOString().slice(0, 19);
    }
    setLoading(true);
    api
      .getHostPlot({ host, end_time__gte, end_time__lte })
      .then((resp) => setData(resp as HostDetailData))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Request failed"),
      )
      .finally(() => setLoading(false));
  }, [host, searchParams]);

  if (loading) return <LoadingMessage message="Loading host plot…" />;
  if (error) return <BannerErrorMessage message={error} />;
  if (!data) return null;

  const hostName = String(data.host ?? host);
  const plot_item = data.plot_item as BokehJsonItem | null | undefined;
  const plot_unavailable_reason =
    typeof data.plot_unavailable_reason === "string"
      ? data.plot_unavailable_reason
      : null;

  return (
    <>
      <PageBreadcrumbs
        items={[
          { label: "Browse", to: "/" },
          {
            label: `Jobs on ${hostName}`,
            to: `/host/${encodeURIComponent(hostName)}`,
          },
          { label: `${hostName} utilization` },
        ]}
      />
      <h1 className="h2 mb-3">{hostName} utilization</h1>
      <p className="text-muted mb-2">
        Time range: {formatDateTime(data.end_time__gte)} —{" "}
        {data.end_time__lte === "now()" ? "Now" : formatDateTime(data.end_time__lte)}
      </p>
      <p className="mb-3">
        <Link href={`/machine/host/${encodeURIComponent(hostName)}/`}>
          View jobs that ran on this host
        </Link>
      </p>
      <div className="graphs">
        <BokehPlotWithLimitation
          item={plot_item}
          id="host-bokeh"
          plotName="Host plot"
          unavailableReason={plot_unavailable_reason}
        />
      </div>
    </>
  );
}
