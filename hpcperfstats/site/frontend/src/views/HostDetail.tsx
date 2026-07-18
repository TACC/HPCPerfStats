"use client";

import { TextLink } from "@/components/TextLink";
import { useMemo } from "react";
import type { BokehJsonItem } from "@/types/bokeh";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehPlotWithLimitation from "../components/BokehPlotWithLimitation";
import LoadingMessage from "../components/LoadingMessage";
import { formatDateTime } from "../utils/formatDateTime";
import { useMachineRouteParams } from "../hooks/use-machine-route-params";
import { useStableSearchParamsKey } from "../hooks/use-stable-search-params";
import { buildAsyncPageTitle } from "../utils/async-page-title";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { useHostPlotQuery } from "@/hooks/use-host-plot";
import { cn } from "@/lib/utils";

/** Host plot identity: host + end_time range only (ignore unrelated QS). */
export function buildHostPlotParamsFromSearch(
  host: string,
  searchParamsKey: string,
): { host: string; end_time__gte: string; end_time__lte: string } | null {
  if (!host) return null;
  const params = new URLSearchParams(searchParamsKey);
  let end_time__gte = params.get("end_time__gte") || "";
  const end_time__lte = params.get("end_time__lte") || "now()";
  if (!end_time__gte) {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    end_time__gte = d.toISOString().slice(0, 19);
  }
  return { host, end_time__gte, end_time__lte };
}

function hostPlotIdentityKey(host: string, searchParamsKey: string): string {
  const params = new URLSearchParams(searchParamsKey);
  return `${host}|${params.get("end_time__gte") || ""}|${params.get("end_time__lte") || ""}`;
}

export default function HostDetail() {
  const { flatParams } = useMachineRouteParams();
  const host = flatParams.host ?? "";
  const searchParamsKey = useStableSearchParamsKey();
  const identityKey = hostPlotIdentityKey(host, searchParamsKey);

  const plotParams = useMemo(
    () => buildHostPlotParamsFromSearch(host, searchParamsKey),
    // identityKey encodes host + end_time only
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scoped identity
    [identityKey],
  );

  const { data, error, loading, detailBusy } = useHostPlotQuery(plotParams);

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

  if (loading) return <LoadingMessage message="Loading host plot…" />;
  if (error && !data) return <BannerErrorMessage message={error} />;
  if (!data) return null;

  const hostName = String(data.host ?? host);
  const plot_item = data.plot_item as BokehJsonItem | null | undefined;
  const plot_unavailable_reason =
    typeof data.plot_unavailable_reason === "string"
      ? data.plot_unavailable_reason
      : null;

  return (
    <div className={cn(detailBusy && "opacity-55")} aria-busy={detailBusy}>
      {detailBusy ? (
        <p className="mb-2 text-sm text-muted-foreground" role="status">
          Updating host plot…
        </p>
      ) : null}
      {error ? (
        <BannerErrorMessage variant="inline" className="mb-3" message={error} />
      ) : null}
      <PageBreadcrumbs
        items={[
          { label: "Browse", to: "/machine/" },
          {
            label: `Jobs on ${hostName}`,
            to: `/machine/host/${encodeURIComponent(hostName)}/`,
          },
          { label: `${hostName} utilization` },
        ]}
      />
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">{hostName} utilization</h1>
      <p className="mb-2 text-muted-foreground">
        Time range: {formatDateTime(data.end_time__gte)} —{" "}
        {data.end_time__lte === "now()" ? "Now" : formatDateTime(data.end_time__lte)}
      </p>
      <p className="mb-3">
        <TextLink href={`/machine/host/${encodeURIComponent(hostName)}/`}>
          View jobs that ran on this host
        </TextLink>
      </p>
      <div className="graphs">
        <BokehPlotWithLimitation
          item={plot_item}
          id="host-bokeh"
          plotName="Host plot"
          unavailableReason={plot_unavailable_reason}
        />
      </div>
    </div>
  );
}
