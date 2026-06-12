import { useParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { BokehJsonItem } from "@/types/bokeh";
import type { TypeDetailData } from "@/types/view-models";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { api } from "@/api";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehPlotWithLimitation from "../components/BokehPlotWithLimitation";
import LoadingMessage from "../components/LoadingMessage";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import { buildAsyncPageTitle } from "../utils/async-page-title";
import { useDocumentTitle } from "../utils/useDocumentTitle";

function routeParamString(value: string | string[] | undefined): string {
  if (Array.isArray(value)) return value[0] ?? "";
  return value ?? "";
}

export default function TypeDetail() {
  const params = useParams();
  const jid = routeParamString(params.jid);
  const typeName = routeParamString(params.typeName);
  const [data, setData] = useState<TypeDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const typeLabel = data?.type_name || typeName;
  useDocumentTitle(
    buildAsyncPageTitle({
      loading: loading && !!jid && !!typeName,
      hasError: !!error,
      loadingTitle: `Loading job ${jid || ""} · ${typeName || ""}`.trim(),
      readyTitle: jid && typeName ? `Job ${jid} · ${typeLabel || typeName}` : "",
      fallbackTitle: "Type detail",
    }),
  );

  useEffect(() => {
    if (!jid || !typeName) return;
    api
      .getTypeDetail(jid, typeName)
      .then((resp) => setData(resp as TypeDetailData))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Request failed"),
      )
      .finally(() => setLoading(false));
  }, [jid, typeName]);

  if (loading) return <LoadingMessage message="Loading type detail…" />;
  if (error) return <BannerErrorMessage message={error} />;
  if (!data) return null;

  const {
    type_name,
    jobid,
    tplot_item,
    tplot_unavailable_reason,
    stats_data = [],
    schema = [],
  } = data as TypeDetailData & {
    jobid?: string;
    tplot_item?: BokehJsonItem | null;
    tplot_unavailable_reason?: string | null;
    stats_data?: Array<[string, unknown[]]>;
    schema?: string[];
  };

  return (
    <>
      <PageBreadcrumbs
        items={[
          { label: "Browse", to: "/" },
          { label: `Job ${jobid}`, to: `/job/${jobid}` },
          { label: String(type_name) },
        ]}
      />
      <p className="mb-2">
        <Link href={`/machine/job/${jobid}/`}>Back to job {jobid}</Link>
      </p>
      <h1 className="h2 mb-3">
        Job {jobid} / Type {type_name}
      </h1>
      <h2 className="h5 mb-2">Rates Aggregated over devices</h2>
      <div className="graphs">
        <BokehPlotWithLimitation
          item={tplot_item}
          id="type-bokeh"
          plotName="Type detail"
          unavailableReason={tplot_unavailable_reason}
        />
      </div>
      <h2 className="h5 mb-2 mt-4">Counts Aggregated over devices and hosts</h2>
      {stats_data.length === 0 ? (
        <p className="text-muted">No counter samples for this type on this job.</p>
      ) : (
        <>
          <div className="table-responsive">
            <table className="table table-sm table-bordered">
              <caption className="visually-hidden">
                Counts aggregated over devices and hosts for job {jobid}
              </caption>
              <thead>
                <tr>
                  <th scope="col">record</th>
                  {schema.map((key) => (
                    <th key={key} scope="col">
                      {key}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stats_data.map(([time, values], i) => (
                  <tr key={i}>
                    <th scope="row">{time}</th>
                    {values.map((v, j) => (
                      <td key={j}>
                        {typeof v === "number" ? formatDecimalStandard(v) : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
