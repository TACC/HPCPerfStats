import { useRouter, usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { memo, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useJobDetailQuery } from "@/hooks/use-job-detail";
import { useJobPlotsQuery } from "@/hooks/use-job-plots";
import type { BokehJsonItem } from "@/types/bokeh";
import type {
  JobDetailData,
  JobMetricCell,
  JobPlotsState,
} from "@/types/view-models";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehEmbed from "../components/BokehEmbed";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { formatDateTime } from "../utils/formatDateTime";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { isSafeHttpUrl } from "../utils/safe-external-url";
import { useSession } from "../session-context";
import { VariableInfoLabel } from "../components/VariableInfoLabel";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import { getJobMetricShortLabel } from "../utils/jobMetricDisplayLabels";
import {
  readTabFromSearchParams,
  searchParamsWithTab,
} from "../utils/sync-tab-search-param";
import { useMachineRouteParams } from "../hooks/use-machine-route-params";
import { JOB_PLOT_CONFIGS } from "@/utils/job-detail-plots";

const JOB_DETAIL_COMPACT_TABLE_CLASS =
  "border text-sm [&_td]:px-[0.45rem] [&_td]:py-[0.2rem] [&_td]:align-middle [&_td]:leading-[1.3] [&_th]:px-[0.45rem] [&_th]:py-[0.2rem] [&_th]:align-middle [&_th]:leading-[1.3]";

export {
  mergeProgressiveJobPlotsState,
  jobPlotEntryEqual,
  jobPlotStatesEqual,
} from "@/utils/job-detail-plots";

type JobAnalysisTab =
  | "metrics"
  | "summary"
  | "roofline"
  | "multiprecisionMix"
  | "processes"
  | "execHosts"
  | "device";

type JobPlotConfig = (typeof JOB_PLOT_CONFIGS)[number];

type JobPlotConfigKey = JobPlotConfig["key"];

type JobMetricRow = JobMetricCell & {
  metric: string;
  units?: string | null;
};

type JobSummaryRow = {
  jid?: string | number;
  username?: string;
  account?: string;
  start_time?: string | null;
  end_time?: string | null;
  runtime?: string | number | null;
  timelimit?: string | number | null;
  queue?: string;
  jobname?: string;
  state?: string;
  ncores?: string | number | null;
  nhosts?: string | number | null;
};

type XaltLibsetEntry = [string, string];
type XaltData = {
  exec_path?: string[];
  cwd?: string[];
  libset?: XaltLibsetEntry[];
};

type JobDetailViewData = JobDetailData & {
  job_data?: JobSummaryRow;
  host_list?: string[];
  fsio?: Record<string, Array<number | null>>;
  xalt_data?: XaltData;
  schema?: Record<string, string[] | string>;
  client_url?: string | null;
  server_url?: string | null;
  gpu_active?: string | number | null;
  gpu_utilization_max?: string | number | null;
  gpu_utilization_mean?: string | number | null;
  gpu_count?: string | number | null;
  multiprecision_cpu_plot_item?: BokehJsonItem | null;
  multiprecision_cpu_unavailable_reason?: string | null;
  multiprecision_gpu_plot_item?: BokehJsonItem | null;
  multiprecision_gpu_unavailable_reason?: string | null;
  metrics_list?: JobMetricRow[];
  proc_list?: string[];
  staff_metrics_distinct_time_count?: string | number | null;
};

type PlotPanelProps = {
  item?: BokehJsonItem | null;
  id: string;
  plotName: string;
  unavailableReason?: string | null;
  isLoading: boolean;
};

type PlotPanelInfo = {
  key: JobPlotConfig["panelKey"];
  item: BokehJsonItem | null;
  isLoading: boolean;
  id: string;
  plotName: string;
  unavailableReason: string | null;
};

const JOB_DETAIL_ANALYSIS_TABS: ReadonlySet<JobAnalysisTab> = new Set([
  "metrics",
  "summary",
  "roofline",
  "multiprecisionMix",
  "processes",
  "execHosts",
  "device",
]);

function formatJobMetricCell(obj: JobMetricCell, isStaff: boolean): string {
  if (obj.value != null && obj.value !== "") {
    return formatDecimalStandard(obj.value);
  }
  if (isStaff) {
    return obj.no_data_reason || "Data not available.";
  }
  return "Data not available.";
}

function buildJobDetailTitle({
  error,
  loading,
  data,
  pk,
}: {
  error: string | null;
  loading: boolean;
  data: JobDetailData | null;
  pk: string;
}): string {
  if (error) return pk ? `Job ${pk} (error)` : "Job detail";
  if (loading && pk) return `Loading job ${pk}`;
  if (data?.job_data?.jid) return `Job ${data.job_data.jid}`;
  return pk ? `Job ${pk}` : "Job detail";
}

function renderJobEntityLink(
  value: string | undefined,
  to: string,
  fallbackText: string,
): ReactNode {
  return value ? <Link href={to}>{value}</Link> : fallbackText;
}

const PlotPanel = memo(function PlotPanel({
  item,
  id,
  plotName,
  unavailableReason,
  isLoading,
}: PlotPanelProps) {
  const plotDescId = `${id}-plot-desc`;
  return (
    <div className="relative min-w-0 w-full">
      <p id={plotDescId} className="sr-only">
        Interactive performance chart. Scales to the available width. Numerical detail may not be read
        by assistive technology.
      </p>
      <BokehEmbed
        item={item}
        id={id}
        plotName={plotName}
        unavailableReason={unavailableReason}
        isLoadingExternal={isLoading}
        wrapperClassName="job-detail-plot-embed w-full min-w-0 min-h-[340px]"
        ariaDescribedBy={plotDescId}
        maximizeInContainer="width"
      />
    </div>
  );
});

export default function JobDetail() {
  const session = useSession();
  const isStaff = !!session?.is_staff;
  const { flatParams } = useMachineRouteParams();
  const pk = flatParams.pk ?? "";
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const {
    data: jobDetailData,
    error,
    loading,
    detailsLoading,
    detailFetchWarning,
  } = useJobDetailQuery(pk);
  const data = jobDetailData as JobDetailViewData | null;
  const rawTab = readTabFromSearchParams(searchParams, "tab", "metrics");
  const analysisTab: JobAnalysisTab = JOB_DETAIL_ANALYSIS_TABS.has(rawTab as JobAnalysisTab)
    ? (rawTab as JobAnalysisTab)
    : "metrics";
  const plotsEnabled =
    !!pk &&
    !loading &&
    !error &&
    !!data &&
    (analysisTab === "summary" || analysisTab === "roofline");
  const { plots, plotsLoading, plotsFetchFailed, retryJobPlots } = useJobPlotsQuery(
    pk,
    plotsEnabled,
  );

  function setAnalysisTab(tab: JobAnalysisTab): void {
    const next = searchParamsWithTab(
      searchParams,
      "tab",
      tab === "metrics" ? null : tab,
    );
    const qs = next.toString();
    const href = qs ? `${pathname}?${qs}` : pathname;
    router.replace(href);
  }

  useDocumentTitle(buildJobDetailTitle({ error, loading, data, pk }));

  if (loading) {
    return (
      <div className="job-detail-skeleton" aria-busy="true">
        <span className="sr-only" role="status" aria-label="Loading job detail">
          Loading job detail
        </span>
        <Skeleton className="mb-3 h-10 w-1/2" />
        <div
          className="job-detail-skeleton-plot mb-4 rounded-lg border p-2"
          aria-hidden="true"
        >
          <Skeleton className="mb-2 h-4 w-2/3" />
          <Skeleton className="min-h-[320px] w-full rounded-md" />
        </div>
      </div>
    );
  }
  if (error) return <BannerErrorMessage message={error} />;
  if (!data) return null;

  const detailData = data as JobDetailViewData;
  const job = detailData.job_data || {};
  const {
    host_list = [],
    fsio = {},
    xalt_data = {},
    schema = {},
    client_url,
    server_url,
    gpu_active,
    gpu_utilization_max,
    gpu_utilization_mean,
    gpu_count,
    multiprecision_cpu_plot_item,
    multiprecision_cpu_unavailable_reason,
    multiprecision_gpu_plot_item,
    multiprecision_gpu_unavailable_reason,
    metrics_list = [],
    proc_list = [],
    staff_metrics_distinct_time_count: staffMetricsDistinctTimeCount,
  } = detailData;

  const gpuStatsTableCellClass = {
    label: "border border-border",
    value: "border border-border text-right",
  };
  const gpuStatsRows = [
    {
      key: "gpu_count",
      label: "Total GPUs allocated:",
      value: formatDecimalStandard(gpu_count),
    },
    {
      key: "gpu_active",
      label: "Number of GPUs active:",
      value: formatDecimalStandard(gpu_active),
    },
    {
      key: "gpu_util_max",
      label: "Max GPU Utilization:",
      value:
        gpu_utilization_max != null && gpu_utilization_max !== ""
          ? `${formatDecimalStandard(gpu_utilization_max)}%`
          : "",
    },
    {
      key: "gpu_util_mean",
      label: "Mean GPU Utilization:",
      value:
        gpu_utilization_mean != null && gpu_utilization_mean !== ""
          ? `${formatDecimalStandard(gpu_utilization_mean)}%`
          : "",
    },
  ];

  const hasDeviceData = Object.keys(schema).length > 0;
  const plotConfigByKey = JOB_PLOT_CONFIGS.reduce<Record<JobPlotConfigKey, JobPlotConfig>>((acc, config) => {
    acc[config.key] = config;
    return acc;
  }, {} as Record<JobPlotConfigKey, JobPlotConfig>);
  const plotPanels: PlotPanelInfo[] = JOB_PLOT_CONFIGS.map((config) => ({
    key: config.panelKey,
    item: plots?.[config.key]?.plotItem ?? null,
    isLoading: !!plots?.[config.key]?.loading,
    id: `${config.idPrefix}-${pk}`,
    plotName: config.plotName,
    unavailableReason: plots?.[config.key]?.unavailableReason ?? null,
  }));

  const metricsListFull = metrics_list || [];
  const metricsSplitIdx = Math.ceil(metricsListFull.length / 2);
  const metricsTableLeft = metricsListFull.slice(0, metricsSplitIdx);
  const metricsTableRight = metricsListFull.slice(metricsSplitIdx);

  function metricTableRows(list: JobMetricRow[]): ReactNode {
    return list.map((obj) => (
      <TableRow key={obj.metric}>
        <TableHead scope="row">
          <span className="inline-flex max-w-full flex-wrap items-baseline gap-x-1 gap-y-[0.15rem]">
            <VariableInfoLabel
              variableName={obj.metric}
              labelText={getJobMetricShortLabel(obj.metric)}
              enableHelp
            />
            {obj.units ? (
              <span className="font-normal whitespace-nowrap text-muted-foreground">[{obj.units}]</span>
            ) : null}
          </span>
        </TableHead>
        <TableCell className={obj.value != null && obj.value !== "" ? "" : "text-muted-foreground"}>
          {formatJobMetricCell(obj, isStaff)}
        </TableCell>
      </TableRow>
    ));
  }

  function renderSinglePlotPanel(config: JobPlotConfig | undefined, isTabActive: boolean): ReactNode {
    if (!config) return null;
    const panel = plotPanels.find((p) => p.key === config.panelKey);
    if (!panel) return null;
    return (
      <div key={config.key} className="mb-3 w-full min-w-0 box-border">
        <h3 className="text-base font-medium">{config.plotName}</h3>
        <p className="job-detail-plots-intro mb-2 max-w-[36rem] text-sm text-muted-foreground">
          Host-level plot for this job. Loads progressively; chart width follows the panel below.
        </p>
        {isTabActive ? (
          <PlotPanel
            item={panel.item}
            id={panel.id}
            plotName={panel.plotName}
            unavailableReason={panel.unavailableReason}
            isLoading={panel.isLoading}
          />
        ) : null}
      </div>
    );
  }

  return (
    <>
      <PageBreadcrumbs
        items={[
          { label: "Browse", to: "/" },
          { label: `Job ${job.jid}` },
        ]}
      />
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">Job {job.jid}</h1>
      {detailFetchWarning ? (
        <Alert className="mb-3 border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100" role="status">
          <AlertDescription className="text-sm">
            Some job details could not be loaded. Showing partial data from a quick load.
          </AlertDescription>
        </Alert>
      ) : null}

      <section id="job-detail-glance" className="mb-4" aria-labelledby="job-detail-glance-heading">
        <h2 id="job-detail-glance-heading" className="text-lg font-medium">
          Job overview
        </h2>
        <Card className="mb-0">
          <CardContent>
            <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <div>
                <div className="text-muted-foreground-foreground">Job ID</div>
                <div>{job.jid}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Status</div>
                <div>{job.state}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Run time (s)</div>
                <div>{formatDecimalStandard(job.runtime)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Queue</div>
                <div>
                  {renderJobEntityLink(
                    job.queue,
                    `/queue/${encodeURIComponent(String(job.queue ?? ""))}/`,
                    ""
                  )}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">User</div>
                <div>
                  {renderJobEntityLink(
                    job.username,
                    `/username/${encodeURIComponent(String(job.username ?? ""))}/`,
                    "Unknown"
                  )}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">Project</div>
                <div>
                  {renderJobEntityLink(
                    job.account,
                    `/account/${encodeURIComponent(String(job.account ?? ""))}/`,
                    "None"
                  )}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">Cores / nodes</div>
                <div>
                  {formatDecimalStandard(job.ncores)} / {formatDecimalStandard(job.nhosts)}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">Start</div>
                <div>{formatDateTime(job.start_time)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">End</div>
                <div>{formatDateTime(job.end_time)}</div>
              </div>
              <div className="col-span-full">
                <div className="text-muted-foreground">Job name</div>
                <div>{job.jobname}</div>
              </div>
              {isStaff ? (
                <div className="col-span-full">
                  <div className="text-muted-foreground">
                    <VariableInfoLabel
                      variableName="metrics_distinct_time_count"
                      labelText="Sample Count"
                      enableHelp
                    />
                  </div>
                  <div>
                    {staffMetricsDistinctTimeCount != null && staffMetricsDistinctTimeCount !== ""
                      ? formatDecimalStandard(staffMetricsDistinctTimeCount)
                      : "Not computed yet."}
                  </div>
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </section>

      <section id="job-detail-scheduling" className="mb-4" aria-labelledby="job-detail-scheduling-heading">
        <h2 id="job-detail-scheduling-heading" className="sr-only">
          Full scheduling record
        </h2>
        <Collapsible className="job-detail-scheduling-details rounded-lg border px-3 py-2">
          <CollapsibleTrigger className="cursor-pointer text-left font-semibold">
            Full scheduling record
            <span className="text-sm font-normal text-muted-foreground"> — all accounting columns</span>
          </CollapsibleTrigger>
          <CollapsibleContent>
          <Table className={cn(JOB_DETAIL_COMPACT_TABLE_CLASS, "mt-2")}>
              <TableCaption className="sr-only">
                Full scheduling record for job {job.jid}
              </TableCaption>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="jid" labelText="Job ID" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="username" labelText="user" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="account" labelText="project" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="start_time" labelText="start time" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="end_time" labelText="end time" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="runtime" labelText="run time (s)" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="timelimit" labelText="requested time (s)" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="queue" labelText="queue" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="jobname" labelText="name" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="state" labelText="status" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="ncores" labelText="ncores" enableHelp />
                  </TableHead>
                  <TableHead scope="col">
                    <VariableInfoLabel variableName="nhosts" labelText="nnodes" enableHelp />
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>
                    <Link href={`/machine/job/${job.jid}/`} className="text-primary hover:underline">{job.jid}</Link>
                  </TableCell>
                  <TableCell>
                    {renderJobEntityLink(
                      job.username,
                      `/username/${encodeURIComponent(String(job.username ?? ""))}/`,
                      "Unknown"
                    )}
                  </TableCell>
                  <TableCell>
                    {renderJobEntityLink(
                      job.account,
                      `/account/${encodeURIComponent(String(job.account ?? ""))}/`,
                      "None"
                    )}
                  </TableCell>
                  <TableCell>{formatDateTime(job.start_time)}</TableCell>
                  <TableCell>{formatDateTime(job.end_time)}</TableCell>
                  <TableCell>{formatDecimalStandard(job.runtime)}</TableCell>
                  <TableCell>{formatDecimalStandard(job.timelimit)}</TableCell>
                  <TableCell>
                    {renderJobEntityLink(
                      job.queue,
                      `/queue/${encodeURIComponent(String(job.queue ?? ""))}/`,
                      ""
                    )}
                  </TableCell>
                  <TableCell>{job.jobname}</TableCell>
                  <TableCell>{job.state}</TableCell>
                  <TableCell>{formatDecimalStandard(job.ncores)}</TableCell>
                  <TableCell>{formatDecimalStandard(job.nhosts)}</TableCell>
                </TableRow>
              </TableBody>
          </Table>
          </CollapsibleContent>
        </Collapsible>
      </section>

      <section id="job-detail-resources" className="mb-4" aria-labelledby="job-detail-resources-heading">
        <h2 id="job-detail-resources-heading" className="text-lg font-medium">
          Resources
        </h2>
        <div className="max-w-4xl">
            <Table className="border text-sm">
                <TableCaption className="sr-only">
                  Shared file system I/O for job {job.jid}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Shared File System</TableHead>
                    <TableHead scope="col">MB Read</TableHead>
                    <TableHead scope="col">MB Written</TableHead>
                    <TableHead scope="col">Peak MB/s</TableHead>
                    <TableHead scope="col">Peak IOPS</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detailsLoading ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground">
                        Loading shared file system data…
                      </TableCell>
                    </TableRow>
                  ) : Object.keys(fsio).length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground">
                        Data not available.
                      </TableCell>
                    </TableRow>
                  ) : (
                    Object.entries(fsio).map(([key, val]) => (
                      <TableRow key={key}>
                        <TableCell>{key}</TableCell>
                        <TableCell>{formatDecimalStandard((val as Array<number | null>)[0])}</TableCell>
                        <TableCell>{formatDecimalStandard((val as Array<number | null>)[1])}</TableCell>
                        <TableCell>
                          {(val as Array<number | null>)[2] != null &&
                          !Number.isNaN((val as Array<number | null>)[2])
                            ? formatDecimalStandard((val as Array<number | null>)[2])
                            : "—"}
                        </TableCell>
                        <TableCell>
                          {(val as Array<number | null>)[3] != null &&
                          !Number.isNaN((val as Array<number | null>)[3])
                            ? formatDecimalStandard((val as Array<number | null>)[3])
                            : "—"}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
            </Table>
        </div>
        {(detailsLoading || gpu_active != null || gpu_count != null) && (
          <div className="mt-3">
            {detailsLoading && gpu_active == null && gpu_count == null ? (
              <p className="text-muted-foreground mb-0" role="status">
                Loading GPU statistics…
              </p>
            ) : (
              <Table className="mb-0 border text-sm">
                <TableBody>
                  {gpuStatsRows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell className={gpuStatsTableCellClass.label}>
                        <b>{row.label}</b>
                      </TableCell>
                      <TableCell className={gpuStatsTableCellClass.value}>{row.value}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {isSafeHttpUrl(client_url) && (
            <a
              href={client_url!}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              Client Logs
            </a>
          )}
          {isSafeHttpUrl(server_url) && (
            <a
              href={server_url!}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              Server Logs
            </a>
          )}
        </div>
      </section>

      <section
        id="job-detail-analysis"
        className="mb-4"
        aria-labelledby="job-detail-analysis-heading"
      >
        <h2 id="job-detail-analysis-heading" className="text-lg font-medium">
          Job data
        </h2>
        <Tabs
          value={analysisTab}
          onValueChange={(value) => setAnalysisTab(value as JobAnalysisTab)}
        >
          <TabsList
            variant="line"
            className="sticky top-0 z-[var(--z-sticky-inpage)] mb-0 w-full justify-start overflow-x-auto border-b border-border bg-background pt-[0.35rem] [scrollbar-width:thin] flex-nowrap"
            aria-label="Job data views"
          >
            <TabsTrigger value="metrics">Metrics</TabsTrigger>
            <TabsTrigger value="summary">Summary plot</TabsTrigger>
            <TabsTrigger value="roofline">Roofline</TabsTrigger>
            <TabsTrigger value="multiprecisionMix">Multiprecision Mix</TabsTrigger>
            <TabsTrigger value="processes">Processes</TabsTrigger>
            <TabsTrigger value="execHosts">Execution and hosts</TabsTrigger>
            <TabsTrigger value="device">Device data</TabsTrigger>
          </TabsList>
        <div className="job-detail-analysis-panel rounded-b-lg border border-t-0 bg-background p-3">
          {plotsLoading ? (
            <p className="mb-2 text-sm text-muted-foreground" role="status">
              Loading job plots…
            </p>
          ) : null}
          {plotsFetchFailed ? (
            <Alert className="border-amber-200 bg-amber-50 py-2 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100" role="alert">
              <AlertDescription className="text-sm">
                <p className="mb-2">Job plots could not be loaded. The table and metrics below are unchanged.</p>
                <Button type="button" variant="outline" size="sm" onClick={retryJobPlots}>
                  Retry plots
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          <TabsContent
            value="summary"
            id="job-detail-panel-plot-summary"
            className="job-detail-single-plot-pane mt-0 [&_.job-detail-plots-intro]:mx-0 [&_.job-detail-plots-intro]:max-w-none [&_.job-detail-plots-intro]:text-start"
          >
            {renderSinglePlotPanel(
              plotConfigByKey.summary_plot,
              analysisTab === "summary",
            )}
          </TabsContent>
          <TabsContent
            value="roofline"
            id="job-detail-panel-plot-roofline"
            className="job-detail-single-plot-pane mt-0 [&_.job-detail-plots-intro]:mx-0 [&_.job-detail-plots-intro]:max-w-none [&_.job-detail-plots-intro]:text-start"
          >
            <p className="mb-2 text-sm text-muted-foreground">CPU and GPU roofline charts for this job.</p>
            {renderSinglePlotPanel(
              plotConfigByKey.roofline,
              analysisTab === "roofline",
            )}
            {renderSinglePlotPanel(
              plotConfigByKey.gpu_roofline,
              analysisTab === "roofline",
            )}
          </TabsContent>
          <TabsContent value="metrics" id="job-detail-panel-metrics" className="mt-0">
            {detailsLoading ? (
              <p className="text-muted-foreground mb-0">Loading job-level metrics…</p>
            ) : !metrics_list.length ? (
              <p className="text-muted-foreground mb-0">Data not available.</p>
            ) : metricsTableRight.length === 0 ? (
              <Table className={cn(JOB_DETAIL_COMPACT_TABLE_CLASS, "job-detail-metrics-table mb-0 w-full")}>
                  <TableCaption className="sr-only">Job-level metrics for job {job.jid}</TableCaption>
                  <TableBody>{metricTableRows(metricsTableLeft)}</TableBody>
              </Table>
            ) : (
              <div className="job-detail-metrics-two-col grid gap-3 lg:grid-cols-2">
                <div>
                  <Table className={cn(JOB_DETAIL_COMPACT_TABLE_CLASS, "job-detail-metrics-table mb-0 w-full")}>
                      <TableCaption className="sr-only">
                        Job-level metrics for job {job.jid} (column 1)
                      </TableCaption>
                      <TableBody>{metricTableRows(metricsTableLeft)}</TableBody>
                  </Table>
                </div>
                <div>
                  <Table className={cn(JOB_DETAIL_COMPACT_TABLE_CLASS, "job-detail-metrics-table mb-0 w-full")}>
                      <TableCaption className="sr-only">
                        Job-level metrics for job {job.jid} (column 2)
                      </TableCaption>
                      <TableBody>{metricTableRows(metricsTableRight)}</TableBody>
                  </Table>
                </div>
              </div>
            )}
          </TabsContent>
          <TabsContent
            value="multiprecisionMix"
            id="job-detail-panel-multiprecision-mix"
            className="job-detail-single-plot-pane mt-0 [&_.job-detail-plots-intro]:mx-0 [&_.job-detail-plots-intro]:max-w-none [&_.job-detail-plots-intro]:text-start"
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <div className="mb-3 w-full min-w-0 box-border">
                  <h3 className="text-base font-medium">CPU Multiprecision Mix</h3>
                  <p className="job-detail-plots-intro mb-2 max-w-[36rem] text-sm text-muted-foreground">
                    Host-level plot for this job. Loads progressively; chart width follows the panel
                    below.
                  </p>
                  {analysisTab === "multiprecisionMix" ? (
                    <PlotPanel
                      item={multiprecision_cpu_plot_item}
                      id={`job-multiprecision-cpu-${pk}`}
                      plotName="CPU Multiprecision Mix"
                      unavailableReason={multiprecision_cpu_unavailable_reason}
                      isLoading={
                        detailsLoading &&
                        !multiprecision_cpu_plot_item &&
                        !multiprecision_cpu_unavailable_reason
                      }
                    />
                  ) : null}
                </div>
              </div>
              <div>
                <div className="mb-3 w-full min-w-0 box-border">
                  <h3 className="text-base font-medium">GPU Multiprecision Mix</h3>
                  <p className="job-detail-plots-intro mb-2 max-w-[36rem] text-sm text-muted-foreground">
                    Host-level plot for this job. Loads progressively; chart width follows the panel
                    below.
                  </p>
                  {analysisTab === "multiprecisionMix" ? (
                    <PlotPanel
                      item={multiprecision_gpu_plot_item}
                      id={`job-multiprecision-gpu-${pk}`}
                      plotName="GPU Multiprecision Mix"
                      unavailableReason={multiprecision_gpu_unavailable_reason}
                      isLoading={
                        detailsLoading &&
                        !multiprecision_gpu_plot_item &&
                        !multiprecision_gpu_unavailable_reason
                      }
                    />
                  ) : null}
                </div>
              </div>
            </div>
          </TabsContent>
          <TabsContent value="processes" id="job-detail-panel-processes" className="mt-0">
            {detailsLoading ? (
              <p className="text-muted-foreground mb-0">Loading processes…</p>
            ) : !(proc_list || []).length ? (
              <p className="text-muted-foreground mb-0">Data not available.</p>
            ) : (
              <Table className="border text-sm">
                <TableCaption className="sr-only">
                  Processes recorded for job {job.jid}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Process</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(proc_list || []).map((proc, i) => (
                    <TableRow key={i}>
                      <TableCell>{proc}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </TabsContent>
          <TabsContent value="execHosts" id="job-detail-panel-exec-hosts" className="mt-0">
            <h3 className="text-base font-medium">Execution parameters</h3>
            {detailsLoading ? (
              <p className="text-muted-foreground">Loading execution parameters…</p>
            ) : (
              <>
                <Table className="border text-sm">
                  <TableCaption className="sr-only">
                    Execution parameters for job {job.jid}
                  </TableCaption>
                  <TableBody>
                    <TableRow>
                      <TableHead scope="row">Executable Path</TableHead>
                      <TableCell>
                        {(xalt_data.exec_path || []).length === 0 ? (
                          <span className="text-muted-foreground">Data not available.</span>
                        ) : (
                          (xalt_data.exec_path || []).map((item: string, i: number) => (
                            <span key={`exec-${i}`}>
                              {item}
                              <br />
                            </span>
                          ))
                        )}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableHead scope="row">Working Directory</TableHead>
                      <TableCell>
                        {(xalt_data.cwd || []).length === 0 ? (
                          <span className="text-muted-foreground">Data not available.</span>
                        ) : (
                          (xalt_data.cwd || []).map((item: string, i: number) => (
                            <span key={`cwd-${i}`}>
                              {item}
                              <br />
                            </span>
                          ))
                        )}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
                <Table className="mt-2 border text-sm">
                  <TableCaption className="sr-only">
                    Modules and libraries for job {job.jid}
                  </TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">Module</TableHead>
                      <TableHead scope="col">Library</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(xalt_data.libset || []).length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={2} className="text-muted-foreground">
                          Data not available.
                        </TableCell>
                      </TableRow>
                    ) : (
                      (xalt_data.libset || []).map((item: XaltLibsetEntry, i: number) => (
                        <TableRow key={i}>
                          <TableCell>{item[1] === "none" ? "system" : item[1]}</TableCell>
                          <TableCell>{item[0]}</TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </>
            )}
            <h3 className="mt-3 text-base font-medium">Hosts</h3>
            {!host_list.length ? (
              <p className="text-muted-foreground mb-0">Data not available.</p>
            ) : (
              <Table className="border text-sm">
                  <TableCaption className="sr-only">
                    Execution hosts for job {job.jid}
                  </TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">Host</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {host_list.map((host, i) => (
                      <TableRow key={i}>
                        <TableCell>{host}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
              </Table>
            )}
          </TabsContent>
          <TabsContent value="device" id="job-detail-panel-device" className="mt-0">
            <div className="text-center text-md-start">
              {detailsLoading ? (
                <p className="text-muted-foreground mb-0" role="status">
                  Loading device data and plots…
                </p>
              ) : !hasDeviceData ? (
                <p className="text-muted-foreground mb-0" role="status">
                  Data not available.
                </p>
              ) : (
                  <Table className={JOB_DETAIL_COMPACT_TABLE_CLASS}>
                    <TableCaption className="sr-only">
                      Device types and events for job {job.jid}
                    </TableCaption>
                    <TableHeader>
                      <TableRow>
                        <TableHead scope="col">Type Name</TableHead>
                        <TableHead scope="col">Recorded Performance Events</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {Object.entries(schema).map(([type_name, event]) => (
                        <TableRow key={type_name}>
                          <TableCell>
                            <Link href={`/machine/job/${job.jid}/${type_name}/`}>{type_name}</Link>
                          </TableCell>
                          <TableCell className="text-left">
                            {Array.isArray(event)
                              ? event.map((ev, i) => (
                                  <span key={ev}>
                                    {i > 0 ? ", " : ""}
                                    <VariableInfoLabel
                                      variableName={ev}
                                      labelText={ev}
                                      enableHelp
                                    />
                                  </span>
                                ))
                              : String(event)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
              )}
            </div>
          </TabsContent>
        </div>
        </Tabs>
      </section>
    </>
  );
}
