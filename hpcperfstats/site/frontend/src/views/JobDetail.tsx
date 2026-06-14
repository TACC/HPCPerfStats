import { useRouter, usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, memo, useId, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { api } from "@/api";
import { useJobDetailQuery } from "@/hooks/use-job-detail";
import type { BokehJsonItem } from "@/types/bokeh";
import type {
  JobDetailData,
  JobMetricCell,
  JobPlotBatchResponse,
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
import { cn } from "@/lib/utils";
import { formatDateTime } from "../utils/formatDateTime";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { isSafeHttpUrl } from "../utils/safe-external-url";
import { useSession } from "../session-context";
import { VariableInfoLabel } from "../components/VariableInfoLabel";
import { scheduleJobPlotsRetry } from "../utils/job-plots-polling";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import { getJobMetricShortLabel } from "../utils/jobMetricDisplayLabels";
import {
  readTabFromSearchParams,
  searchParamsWithTab,
} from "../utils/sync-tab-search-param";
import { useArrowKeyTabs } from "../hooks/useArrowKeyTabs";
import { useMachineRouteParams } from "../hooks/use-machine-route-params";

type JobAnalysisTab =
  | "metrics"
  | "summary"
  | "roofline"
  | "multiprecisionMix"
  | "processes"
  | "execHosts"
  | "device";

type JobPlotConfig = {
  key: "summary_plot" | "roofline" | "gpu_roofline";
  panelKey: "summary" | "roofline-cpu" | "roofline-gpu";
  idPrefix: string;
  plotName: string;
};

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

type PlotBatchFields = { item: string; reason: string };

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
    <div className="job-detail-plot-embed-host">
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
        wrapperClassName="job-detail-plot-embed"
        ariaDescribedBy={plotDescId}
        maximizeInContainer="width"
      />
    </div>
  );
});

const JOB_PLOT_CONFIGS: ReadonlyArray<JobPlotConfig> = [
  {
    key: "summary_plot",
    panelKey: "summary",
    idPrefix: "job-mscript",
    plotName: "Summary plot",
  },
  {
    key: "roofline",
    panelKey: "roofline-cpu",
    idPrefix: "job-roofline",
    plotName: "CPU Roofline",
  },
  {
    key: "gpu_roofline",
    panelKey: "roofline-gpu",
    idPrefix: "job-gpu-roofline",
    plotName: "GPU Roofline (PCIe/NvLink)",
  },
];

function createEmptyJobPlotsState(loading: boolean): JobPlotsState {
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    acc[config.key] = {
      loading,
      plotItem: null,
      unavailableReason: null,
    };
    return acc;
  }, {});
}

const analysisTabTriggerClass = (isActive: boolean) =>
  cn(
    "inline-flex items-center justify-center rounded-t-md border border-transparent px-3 py-1.5 text-sm font-medium -mb-px transition-colors",
    "hover:border-border hover:border-b-transparent",
    isActive && "border-border border-b-transparent bg-background text-foreground",
  );

/** Maps React plot keys to `job_plots` batch payload fields (plot=all). */
const JOB_PLOTS_BATCH_FIELDS: Record<JobPlotConfigKey, PlotBatchFields> = {
  summary_plot: { item: "mplot_item", reason: "mplot_unavailable_reason" },
  roofline: { item: "rplot_item", reason: "rplot_unavailable_reason" },
  gpu_roofline: { item: "grplot_item", reason: "grplot_unavailable_reason" },
};

function plotsStateFromBatchResponse(resp: JobPlotBatchResponse): JobPlotsState {
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    const fields = JOB_PLOTS_BATCH_FIELDS[config.key];
    acc[config.key] = {
      loading: false,
      plotItem: (resp[fields.item] as BokehJsonItem | null | undefined) ?? null,
      unavailableReason: (resp[fields.reason] as string | null | undefined) ?? null,
    };
    return acc;
  }, {});
}

/** Merge a progressive `job_plots` partial payload into existing per-plot state. */
export function mergeProgressiveJobPlotsState(
  prevPlots: JobPlotsState | null,
  resp: JobPlotBatchResponse,
): JobPlotsState {
  const loadingSet = new Set(resp.loading_plots ?? []);
  return JOB_PLOT_CONFIGS.reduce<JobPlotsState>((acc, config) => {
    const fields = JOB_PLOTS_BATCH_FIELDS[config.key];
    const previous = prevPlots?.[config.key] ?? {
      loading: true,
      plotItem: null,
      unavailableReason: null,
    };
    if (loadingSet.has(config.key)) {
      acc[config.key] = {
        loading: true,
        plotItem: previous.plotItem,
        unavailableReason: previous.unavailableReason,
      };
      return acc;
    }
    if (Object.hasOwn(resp, fields.item)) {
      acc[config.key] = {
        loading: false,
        plotItem: (resp[fields.item] as BokehJsonItem | null | undefined) ?? null,
        unavailableReason: (resp[fields.reason] as string | null | undefined) ?? null,
      };
      return acc;
    }
    acc[config.key] = { ...previous, loading: true };
    return acc;
  }, {});
}

export function jobPlotEntryEqual(
  p: JobPlotsState[string] | null | undefined,
  q: JobPlotsState[string] | null | undefined,
): boolean {
  if (p === q) return true;
  if (!p || !q) return false;
  if (p.loading !== q.loading || p.unavailableReason !== q.unavailableReason) return false;
  if (p.plotItem === q.plotItem) return true;
  if (p.plotItem == null && q.plotItem == null) return true;
  if (p.plotItem == null || q.plotItem == null) return false;
  try {
    return JSON.stringify(p.plotItem) === JSON.stringify(q.plotItem);
  } catch {
    return false;
  }
}

export function jobPlotStatesEqual(
  a: JobPlotsState | null | undefined,
  b: JobPlotsState | null | undefined,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return JOB_PLOT_CONFIGS.every((cfg) => jobPlotEntryEqual(a[cfg.key], b[cfg.key]));
}

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
  const [plots, setPlots] = useState<JobPlotsState | null>(null);
  const [plotsLoading, setPlotsLoading] = useState(true);
  const [plotsFetchFailed, setPlotsFetchFailed] = useState(false);
  const plotsFetchGenRef = useRef(0);
  const rawTab = readTabFromSearchParams(searchParams, "tab", "metrics");
  const analysisTab: JobAnalysisTab = JOB_DETAIL_ANALYSIS_TABS.has(rawTab as JobAnalysisTab)
    ? (rawTab as JobAnalysisTab)
    : "metrics";

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
  const tabMetricsId = useId();
  const tabProcessesId = useId();
  const tabExecHostsId = useId();
  const tabDeviceId = useId();
  const tabPlotSummaryId = useId();
  const tabPlotRooflineId = useId();
  const tabMultiprecisionMixId = useId();

  const plotTabDomIds: Record<"summary" | "roofline", string> = {
    summary: tabPlotSummaryId,
    roofline: tabPlotRooflineId,
  };

  const analysisTabButtonIds = useMemo(
    () => [
      tabMetricsId,
      tabPlotSummaryId,
      tabPlotRooflineId,
      tabMultiprecisionMixId,
      tabProcessesId,
      tabExecHostsId,
      tabDeviceId,
    ],
    [
      tabMetricsId,
      tabPlotSummaryId,
      tabPlotRooflineId,
      tabMultiprecisionMixId,
      tabProcessesId,
      tabExecHostsId,
      tabDeviceId,
    ],
  );

  const analysisTabIdToKey = useMemo<Record<string, JobAnalysisTab>>(
    () => ({
      [tabMetricsId]: "metrics",
      [tabPlotSummaryId]: "summary",
      [tabPlotRooflineId]: "roofline",
      [tabMultiprecisionMixId]: "multiprecisionMix",
      [tabProcessesId]: "processes",
      [tabExecHostsId]: "execHosts",
      [tabDeviceId]: "device",
    }),
    [
      tabMetricsId,
      tabPlotSummaryId,
      tabPlotRooflineId,
      tabMultiprecisionMixId,
      tabProcessesId,
      tabExecHostsId,
      tabDeviceId,
    ],
  );

  const activeAnalysisTabButtonId = useMemo(() => {
    const entry = Object.entries(analysisTabIdToKey).find(([, key]) => key === analysisTab);
    return entry ? entry[0] : tabMetricsId;
  }, [analysisTab, analysisTabIdToKey, tabMetricsId]);

  const handleAnalysisTabKeyDown = useArrowKeyTabs(
    analysisTabButtonIds,
    activeAnalysisTabButtonId,
    (nextTabButtonId: string) => {
      const nextKey = analysisTabIdToKey[nextTabButtonId];
      if (nextKey) setAnalysisTab(nextKey);
    },
  );

  useDocumentTitle(buildJobDetailTitle({ error, loading, data, pk }));

  const fetchAllJobPlotsWithPolling = useCallback(
    async (cancelledCheck: () => boolean): Promise<void> => {
      let keepLoading = false;
      try {
        const plotResponse = (await api.getJobPlots(pk, null, false, true)) as JobPlotBatchResponse;
        if (cancelledCheck()) return;

        if (plotResponse?.status === "loading") {
          keepLoading = true;
          scheduleJobPlotsRetry(
            () => fetchAllJobPlotsWithPolling(cancelledCheck),
            plotResponse.retry_after_seconds,
            cancelledCheck,
          );
          return;
        }

        if (plotResponse?.status === "partial" && plotResponse?.progressive) {
          keepLoading = true;
          setPlotsFetchFailed(false);
          setPlots((prev) => {
            const merged = mergeProgressiveJobPlotsState(prev, plotResponse);
            return jobPlotStatesEqual(prev, merged) ? prev : merged;
          });
          scheduleJobPlotsRetry(
            () => fetchAllJobPlotsWithPolling(cancelledCheck),
            plotResponse.retry_after_seconds,
            cancelledCheck,
          );
          return;
        }

        if (
          plotResponse &&
          typeof plotResponse === "object" &&
          Object.hasOwn(plotResponse, "mplot_item")
        ) {
          setPlotsFetchFailed(false);
          setPlots((prev) => {
            const next = plotsStateFromBatchResponse(plotResponse);
            return jobPlotStatesEqual(prev, next) ? prev : next;
          });
        } else {
          setPlots(createEmptyJobPlotsState(false));
        }
      } catch {
        if (cancelledCheck()) return;
        setPlotsFetchFailed(true);
        setPlots(createEmptyJobPlotsState(false));
      } finally {
        if (cancelledCheck() || keepLoading) return;
        setPlotsLoading(false);
      }
    },
    [pk],
  );

  const retryJobPlots = useCallback(() => {
    setPlotsFetchFailed(false);
    setPlotsLoading(true);
    setPlots(createEmptyJobPlotsState(true));
    plotsFetchGenRef.current += 1;
    const gen = plotsFetchGenRef.current;
    void fetchAllJobPlotsWithPolling(() => plotsFetchGenRef.current !== gen);
  }, [fetchAllJobPlotsWithPolling]);

  useEffect(() => {
    if (!pk || loading || error || !data) return;

    let cancelled = false;
    const cancelledCheck = (): boolean => cancelled;

    setPlots(null);
    setPlotsLoading(true);
    setPlotsFetchFailed(false);
    setPlots(createEmptyJobPlotsState(true));
    void fetchAllJobPlotsWithPolling(cancelledCheck);

    return () => {
      cancelled = true;
    };
  }, [pk, loading, error, data, fetchAllJobPlotsWithPolling]);

  useEffect(() => {
    if (!plots) return;
    const anyPlotReady = JOB_PLOT_CONFIGS.some(
      (config) => plots?.[config.key] && plots[config.key].loading === false
    );
    if (anyPlotReady) setPlotsLoading(false);
  }, [plots]);

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

  const gpuStatsTableCellStyle: { label: CSSProperties; value: CSSProperties } = {
    label: { border: "1px solid lightgrey" },
    value: { border: "1px solid lightgrey", textAlign: "right" },
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
      <tr key={obj.metric}>
        <th scope="row">
          <span className="job-detail-metric-name">
            <VariableInfoLabel
              variableName={obj.metric}
              labelText={getJobMetricShortLabel(obj.metric)}
              enableHelp
            />
            {obj.units ? (
              <span className="job-detail-metric-units">[{obj.units}]</span>
            ) : null}
          </span>
        </th>
        <td className={obj.value != null && obj.value !== "" ? "" : "text-muted-foreground"}>
          {formatJobMetricCell(obj, isStaff)}
        </td>
      </tr>
    ));
  }

  function renderSinglePlotPanel(config: JobPlotConfig | undefined, isTabActive: boolean): ReactNode {
    if (!config) return null;
    const panel = plotPanels.find((p) => p.key === config.panelKey);
    if (!panel) return null;
    return (
      <div key={config.key} className="job-detail-single-plot-host mb-3 w-full">
        <h3 className="text-base font-medium">{config.plotName}</h3>
        <p className="job-detail-plots-intro mb-2 text-sm text-muted-foreground">
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
        <details className="job-detail-scheduling-details rounded-lg border px-3 py-2">
          <summary className="cursor-pointer font-semibold">
            Full scheduling record
            <span className="text-sm font-normal text-muted-foreground"> — all accounting columns</span>
          </summary>
          <Table className="job-detail-compact-table mt-2 border text-sm">
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
        </details>
      </section>

      <section id="job-detail-resources" className="mb-4" aria-labelledby="job-detail-resources-heading">
        <h2 id="job-detail-resources-heading" className="text-lg font-medium">
          Resources
        </h2>
        <div className="max-w-4xl">
            <Table className="border text-sm">
                <caption className="sr-only">
                  Shared file system I/O for job {job.jid}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Shared File System</th>
                    <th scope="col">MB Read</th>
                    <th scope="col">MB Written</th>
                    <th scope="col">Peak MB/s</th>
                    <th scope="col">Peak IOPS</th>
                  </tr>
                </thead>
                <tbody>
                  {detailsLoading ? (
                    <tr>
                      <td colSpan={5} className="text-muted-foreground">
                        Loading shared file system data…
                      </td>
                    </tr>
                  ) : Object.keys(fsio).length === 0 ? (
                    <tr>
                      <td colSpan={5} className="text-muted-foreground">
                        Data not available.
                      </td>
                    </tr>
                  ) : (
                    Object.entries(fsio).map(([key, val]) => (
                      <tr key={key}>
                        <td>{key}</td>
                        <td>{formatDecimalStandard((val as Array<number | null>)[0])}</td>
                        <td>{formatDecimalStandard((val as Array<number | null>)[1])}</td>
                        <td>
                          {(val as Array<number | null>)[2] != null &&
                          !Number.isNaN((val as Array<number | null>)[2])
                            ? formatDecimalStandard((val as Array<number | null>)[2])
                            : "—"}
                        </td>
                        <td>
                          {(val as Array<number | null>)[3] != null &&
                          !Number.isNaN((val as Array<number | null>)[3])
                            ? formatDecimalStandard((val as Array<number | null>)[3])
                            : "—"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
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
                      <TableCell style={gpuStatsTableCellStyle.label}>
                        <b>{row.label}</b>
                      </TableCell>
                      <TableCell style={gpuStatsTableCellStyle.value}>{row.value}</TableCell>
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
        <div
          className="job-detail-analysis-tabs job-detail-tab-scroll mb-0 flex border-b"
          role="tablist"
          aria-label="Job data views"
        >
            <button
              type="button"
              className={analysisTabTriggerClass(analysisTab === "metrics")}
              id={tabMetricsId}
              role="tab"
              aria-selected={analysisTab === "metrics"}
              aria-controls="job-detail-panel-metrics"
              tabIndex={analysisTab === "metrics" ? 0 : -1}
              onClick={() => setAnalysisTab("metrics")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => handleAnalysisTabKeyDown(e, tabMetricsId)}
            >
              Metrics
            </button>
            <button
              type="button"
              className={analysisTabTriggerClass(analysisTab === "summary")}
              id={plotTabDomIds.summary}
              role="tab"
              aria-selected={analysisTab === "summary"}
              aria-controls="job-detail-panel-plot-summary"
              tabIndex={analysisTab === "summary" ? 0 : -1}
              onClick={() => setAnalysisTab("summary")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => handleAnalysisTabKeyDown(e, plotTabDomIds.summary)}
            >
              Summary plot
            </button>
            <button
              type="button"
              className={analysisTabTriggerClass(analysisTab === "roofline")}
              id={plotTabDomIds.roofline}
              role="tab"
              aria-selected={analysisTab === "roofline"}
              aria-controls="job-detail-panel-plot-roofline"
              tabIndex={analysisTab === "roofline" ? 0 : -1}
              onClick={() => setAnalysisTab("roofline")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => handleAnalysisTabKeyDown(e, plotTabDomIds.roofline)}
            >
              Roofline
            </button>
            <button
              type="button"
              className={analysisTabTriggerClass(analysisTab === "multiprecisionMix")}
              id={tabMultiprecisionMixId}
              role="tab"
              aria-selected={analysisTab === "multiprecisionMix"}
              aria-controls="job-detail-panel-multiprecision-mix"
              tabIndex={analysisTab === "multiprecisionMix" ? 0 : -1}
              onClick={() => setAnalysisTab("multiprecisionMix")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => handleAnalysisTabKeyDown(e, tabMultiprecisionMixId)}
            >
              Multiprecision Mix
            </button>
            <button
              type="button"
              className={analysisTabTriggerClass(analysisTab === "processes")}
              id={tabProcessesId}
              role="tab"
              aria-selected={analysisTab === "processes"}
              aria-controls="job-detail-panel-processes"
              tabIndex={analysisTab === "processes" ? 0 : -1}
              onClick={() => setAnalysisTab("processes")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => handleAnalysisTabKeyDown(e, tabProcessesId)}
            >
              Processes
            </button>
            <button
              type="button"
              className={analysisTabTriggerClass(analysisTab === "execHosts")}
              id={tabExecHostsId}
              role="tab"
              aria-selected={analysisTab === "execHosts"}
              aria-controls="job-detail-panel-exec-hosts"
              tabIndex={analysisTab === "execHosts" ? 0 : -1}
              onClick={() => setAnalysisTab("execHosts")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => handleAnalysisTabKeyDown(e, tabExecHostsId)}
            >
              Execution and hosts
            </button>
            <button
              type="button"
              className={analysisTabTriggerClass(analysisTab === "device")}
              id={tabDeviceId}
              role="tab"
              aria-selected={analysisTab === "device"}
              aria-controls="job-detail-panel-device"
              tabIndex={analysisTab === "device" ? 0 : -1}
              onClick={() => setAnalysisTab("device")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) => handleAnalysisTabKeyDown(e, tabDeviceId)}
            >
              Device data
            </button>
        </div>
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
          <div
            id="job-detail-panel-plot-summary"
            role="tabpanel"
            aria-labelledby={plotTabDomIds.summary}
            className="job-detail-single-plot-pane"
            hidden={analysisTab !== "summary"}
          >
            {renderSinglePlotPanel(
              plotConfigByKey.summary_plot,
              analysisTab === "summary",
            )}
          </div>
          <div
            id="job-detail-panel-plot-roofline"
            role="tabpanel"
            aria-labelledby={plotTabDomIds.roofline}
            className="job-detail-single-plot-pane"
            hidden={analysisTab !== "roofline"}
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
          </div>
          <div
            id="job-detail-panel-metrics"
            role="tabpanel"
            aria-labelledby={tabMetricsId}
            hidden={analysisTab !== "metrics"}
          >
            {detailsLoading ? (
              <p className="text-muted-foreground mb-0">Loading job-level metrics…</p>
            ) : !metrics_list.length ? (
              <p className="text-muted-foreground mb-0">Data not available.</p>
            ) : metricsTableRight.length === 0 ? (
              <Table className="job-detail-metrics-table job-detail-compact-table mb-0 border text-sm">
                  <caption className="sr-only">Job-level metrics for job {job.jid}</caption>
                  <tbody>{metricTableRows(metricsTableLeft)}</tbody>
              </Table>
            ) : (
              <div className="grid gap-3 job-detail-metrics-two-col lg:grid-cols-2">
                <div>
                  <Table className="job-detail-metrics-table job-detail-compact-table mb-0 border text-sm">
                      <caption className="sr-only">
                        Job-level metrics for job {job.jid} (column 1)
                      </caption>
                      <tbody>{metricTableRows(metricsTableLeft)}</tbody>
                  </Table>
                </div>
                <div>
                  <Table className="job-detail-metrics-table job-detail-compact-table mb-0 border text-sm">
                      <caption className="sr-only">
                        Job-level metrics for job {job.jid} (column 2)
                      </caption>
                      <tbody>{metricTableRows(metricsTableRight)}</tbody>
                  </Table>
                </div>
              </div>
            )}
          </div>
          <div
            id="job-detail-panel-multiprecision-mix"
            role="tabpanel"
            aria-labelledby={tabMultiprecisionMixId}
            className="job-detail-single-plot-pane"
            hidden={analysisTab !== "multiprecisionMix"}
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <div className="job-detail-single-plot-host mb-3 w-full">
                  <h3 className="text-base font-medium">CPU Multiprecision Mix</h3>
                  <p className="job-detail-plots-intro mb-2 text-sm text-muted-foreground">
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
                <div className="job-detail-single-plot-host mb-3 w-full">
                  <h3 className="text-base font-medium">GPU Multiprecision Mix</h3>
                  <p className="job-detail-plots-intro mb-2 text-sm text-muted-foreground">
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
          </div>
          <div
            id="job-detail-panel-processes"
            role="tabpanel"
            aria-labelledby={tabProcessesId}
            hidden={analysisTab !== "processes"}
          >
            {detailsLoading ? (
              <p className="text-muted-foreground mb-0">Loading processes…</p>
            ) : !(proc_list || []).length ? (
              <p className="text-muted-foreground mb-0">Data not available.</p>
            ) : (
              <Table className="border text-sm">
                  <caption className="sr-only">
                    Processes recorded for job {job.jid}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Process</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(proc_list || []).map((proc, i) => (
                      <tr key={i}>
                        <td>{proc}</td>
                      </tr>
                    ))}
                  </tbody>
              </Table>
            )}
          </div>
          <div
            id="job-detail-panel-exec-hosts"
            role="tabpanel"
            aria-labelledby={tabExecHostsId}
            hidden={analysisTab !== "execHosts"}
          >
            <h3 className="text-base font-medium">Execution parameters</h3>
            {detailsLoading ? (
              <p className="text-muted-foreground">Loading execution parameters…</p>
            ) : (
              <>
                <Table className="border text-sm">
                  <caption className="sr-only">
                    Execution parameters for job {job.jid}
                  </caption>
                  <tbody>
                    <tr>
                      <th scope="row">Executable Path</th>
                      <td>
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
                      </td>
                    </tr>
                    <tr>
                      <th scope="row">Working Directory</th>
                      <td>
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
                      </td>
                    </tr>
                  </tbody>
                </Table>
                <Table className="mt-2 border text-sm">
                  <caption className="sr-only">
                    Modules and libraries for job {job.jid}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Module</th>
                      <th scope="col">Library</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(xalt_data.libset || []).length === 0 ? (
                      <tr>
                        <td colSpan={2} className="text-muted-foreground">
                          Data not available.
                        </td>
                      </tr>
                    ) : (
                      (xalt_data.libset || []).map((item: XaltLibsetEntry, i: number) => (
                        <tr key={i}>
                          <td>{item[1] === "none" ? "system" : item[1]}</td>
                          <td>{item[0]}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </Table>
              </>
            )}
            <h3 className="mt-3 text-base font-medium">Hosts</h3>
            {!host_list.length ? (
              <p className="text-muted-foreground mb-0">Data not available.</p>
            ) : (
              <Table className="border text-sm">
                  <caption className="sr-only">
                    Execution hosts for job {job.jid}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Host</th>
                    </tr>
                  </thead>
                  <tbody>
                    {host_list.map((host, i) => (
                      <tr key={i}>
                        <td>{host}</td>
                      </tr>
                    ))}
                  </tbody>
              </Table>
            )}
          </div>
          <div
            id="job-detail-panel-device"
            role="tabpanel"
            aria-labelledby={tabDeviceId}
            hidden={analysisTab !== "device"}
          >
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
                  <Table className="job-detail-compact-table border text-sm">
                    <caption className="sr-only">
                      Device types and events for job {job.jid}
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Type Name</th>
                        <th scope="col">Recorded Performance Events</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(schema).map(([type_name, event]) => (
                        <tr key={type_name}>
                          <td>
                            <Link href={`/machine/job/${job.jid}/${type_name}/`}>{type_name}</Link>
                          </td>
                          <td style={{ textAlign: "left" }}>
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
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
              )}
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
