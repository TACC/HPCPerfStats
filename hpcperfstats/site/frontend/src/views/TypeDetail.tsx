import { TextLink } from "@/components/TextLink";
import type { BokehJsonItem } from "@/types/bokeh";
import type { TypeDetailData } from "@/types/view-models";
import { formatDecimalStandard } from "../utils/formatDecimal";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehPlotWithLimitation from "../components/BokehPlotWithLimitation";
import LoadingMessage from "../components/LoadingMessage";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMachineRouteParams } from "../hooks/use-machine-route-params";
import { buildAsyncPageTitle } from "../utils/async-page-title";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { useTypeDetailQuery } from "@/hooks/use-type-detail";

export default function TypeDetail() {
  const { flatParams } = useMachineRouteParams();
  const jid = flatParams.jid ?? "";
  const typeName = flatParams.typeName ?? "";
  const { data, error, loading } = useTypeDetailQuery(jid, typeName);

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
          { label: "Browse", to: "/machine/" },
          { label: `Job ${jobid}`, to: `/machine/job/${jobid}/` },
          { label: String(type_name) },
        ]}
      />
      <p className="mb-2">
        <TextLink href={`/machine/job/${jobid}/?tab=device`}>
          Back to job {jobid}
        </TextLink>
      </p>
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">
        Job {jobid} / Type {type_name}
      </h1>
      <h2 className="mb-2 text-lg font-medium">Rates Aggregated over devices</h2>
      <div className="graphs">
        <BokehPlotWithLimitation
          item={tplot_item}
          id="type-bokeh"
          plotName="Type detail"
          unavailableReason={tplot_unavailable_reason}
        />
      </div>
      <h2 className="mt-4 mb-2 text-lg font-medium">Counts Aggregated over devices and hosts</h2>
      {stats_data.length === 0 ? (
        <p className="text-muted-foreground">No counter samples for this type on this job.</p>
      ) : (
        <Table className="border text-sm">
          <TableCaption className="sr-only">
            Counts aggregated over devices and hosts for job {jobid}
          </TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">record</TableHead>
              {schema.map((key) => (
                <TableHead key={key} scope="col">
                  {key}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {stats_data.map(([time, values], i) => (
              <TableRow key={i}>
                <TableHead scope="row">{time}</TableHead>
                {values.map((v, j) => (
                  <TableCell key={j}>
                    {typeof v === "number" ? formatDecimalStandard(v) : String(v)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </>
  );
}
