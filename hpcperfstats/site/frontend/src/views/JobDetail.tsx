"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { memo, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { TextLink } from "@/components/TextLink";
import { useJobDetailQuery } from "@/hooks/use-job-detail";
import { useJobPlotsQuery } from "@/hooks/use-job-plots";
import type { BokehJsonItem } from "@/types/bokeh";
import type { JobDetailResponse } from "@/api/generated/models/jobDetailResponse";
import type {
  JobMetricCell,
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
import { formatGpuClockThrottleReasons } from "../utils/gpuClockThrottleReasons";
import { isSafeHttpUrl } from "../utils/safe-external-url";
import { useSession } from "../session-context";
import { VariableInfoLabel } from "../components/VariableInfoLabel";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import {
  getJobMetricShortLabel,
  getJobWattHoursResourcesTitle,
  getJobWattHoursShortLabel,
  jobHasGpuForWattHoursLabel,
} from "../utils/jobMetricDisplayLabels";
import { groupJobMetricsBySourceSection } from "../utils/jobMetricSourceSections";
import {
  readTabFromSearchParams,
} from "../utils/sync-tab-search-param";
import { useMachineRouteParams } from "../hooks/use-machine-route-params";
import { replaceTabInHistory } from "../utils/replace-tab-history";
import { JOB_PLOT_CONFIGS } from "@/utils/job-detail-plots";

const JOB_DETAIL_COMPACT_TABLE_CLASS =
  "border text-sm [&_td]:px-[0.45rem] [&_td]:py-[0.2rem] [&_td]:align-middle [&_td]:leading-[1.3] [&_th]:px-[0.45rem] [&_th]:py-[0.2rem] [&_th]:align-middle [&_th]:leading-[1.3]";

/** Format distinct stored artifact schema ints for staff diagnostics. */
export function formatArtifactSchemaList(values: number[] | undefined | null): string {
  if (!values || values.length === 0) return "none";
  if (values.length === 1) return String(values[0]);
  const sorted = [...values].sort((a, b) => a - b);
  const consecutive =
    sorted[sorted.length - 1]! - sorted[0]! === sorted.length - 1 &&
    sorted.every((n, i) => i === 0 || n === sorted[i - 1]! + 1);
  if (consecutive) return `${sorted[0]}–${sorted[sorted.length - 1]}`;
  return sorted.join(", ");
}

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

type JobMetricDisplayRow = JobMetricCell & {
  metric: string;
  units?: string | null;
  /** Host-data / catalog lineage from API metrics_list[].type */
  type?: string | null;
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

type JobDetailViewData = JobDetailResponse & {
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
  metrics_list?: JobMetricDisplayRow[];
  proc_list?: ProcListEntry[];
  staff_metrics_distinct_time_count?: string | number | null;
  staff_artifact_contract?: JobDetailResponse["staff_artifact_contract"];
};

type PlotPanelProps = {
  item?: BokehJsonItem | null;
  id: string;
  plotName: string;
  unavailableReason?: string | null;
  isLoading: boolean;
  /** Default ``"width"``; set false for fixed-size pies that must not stretch. */
  maximizeInContainer?: boolean | "width";
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

function fsioResourceLabel(key: string): string {
  if (key === "llite") return "Lustre";
  if (key === "nfs") return "NFS";
  return key;
}

function formatJobMetricCell(
  obj: JobMetricCell & { metric?: string },
  isStaff: boolean,
  ncores?: string | number | null,
  gpuCount?: string | number | null,
): string {
  if (obj.value != null && obj.value !== "") {
    if (obj.metric === "max_gpu_clock_event_reasons") {
      const decoded = formatGpuClockThrottleReasons(Number(obj.value));
      if (decoded) return decoded;
    }
    const formatted = formatDecimalStandard(obj.value);
    if (
      obj.metric === "avg_cpuusage" &&
      ncores != null &&
      ncores !== "" &&
      !Number.isNaN(Number(ncores))
    ) {
      return `${formatted} out of ${formatDecimalStandard(ncores)}`;
    }
    if (
      (obj.metric === "detail_gpu_util_mean" || obj.metric === "detail_gpu_util_max") &&
      gpuCount != null &&
      gpuCount !== "" &&
      !Number.isNaN(Number(gpuCount))
    ) {
      const outOf = Number(gpuCount) * 100;
      return `${formatted} out of ${formatDecimalStandard(outOf)}`;
    }
    return formatted;
  }
  if (isStaff) {
    return obj.no_data_reason || "Data not available.";
  }
  return "Data not available.";
}

function showMetricUnitsSuffix(
  obj: JobMetricDisplayRow,
): boolean {
  if (!obj.units) return false;
  // Decoded throttle flags are prose; hide the wire units [#].
  if (
    obj.metric === "max_gpu_clock_event_reasons" &&
    obj.value != null &&
    obj.value !== "" &&
    formatGpuClockThrottleReasons(Number(obj.value))
  ) {
    return false;
  }
  return true;
}

const EFFECTIVE_VECTOR_WIDTH_METRIC = "avg_vector_width_combined";
const EFFECTIVE_VECTOR_WIDTH_LABEL = "Effective vector width (DP / SP)";

/** Combine DP/SP effective vector width into one Metrics-tab display row. */
function buildMetricsDisplayList(metrics: JobMetricDisplayRow[]): JobMetricDisplayRow[] {
  const width64 = metrics.find((m) => m.metric === "avg_vector_width_64b");
  const width32 = metrics.find((m) => m.metric === "avg_vector_width_32b");
  if (!width64 && !width32) {
    return metrics;
  }

  const dpFormatted =
    width64?.value != null && width64.value !== ""
      ? formatDecimalStandard(width64.value)
      : null;
  const spFormatted =
    width32?.value != null && width32.value !== ""
      ? formatDecimalStandard(width32.value)
      : null;
  let combinedValue: string | number | null = null;
  if (dpFormatted != null && spFormatted != null) {
    combinedValue = `${dpFormatted} / ${spFormatted}`;
  } else if (dpFormatted != null) {
    combinedValue = dpFormatted;
  } else if (spFormatted != null) {
    combinedValue = spFormatted;
  }

  const combined: JobMetricDisplayRow = {
    metric: EFFECTIVE_VECTOR_WIDTH_METRIC,
    value: combinedValue,
    units: null,
    type: width64?.type ?? width32?.type ?? "pmc",
    no_data_reason:
      combinedValue == null
        ? width64?.no_data_reason || width32?.no_data_reason || null
        : null,
  };

  const out: JobMetricDisplayRow[] = [];
  let inserted = false;
  for (const row of metrics) {
    if (row.metric === "avg_vector_width_64b" || row.metric === "avg_vector_width_32b") {
      if (!inserted) {
        out.push(combined);
        inserted = true;
      }
      continue;
    }
    out.push(row);
  }
  return out;
}

type ProcListObject = {
  host?: string | number | null;
  proc?: string | number | null;
  device?: string | number | null;
  uid?: string | number | null;
  vm_rss?: string | number | null;
  vm_hwm?: string | number | null;
  vm_size?: string | number | null;
  threads?: string | number | null;
};

type ProcListEntry = string | ProcListObject;

const PROC_TABLE_COLUMNS: ReadonlyArray<{ key: keyof ProcListObject; label: string }> = [
  { key: "proc", label: "Process" },
  { key: "host", label: "Host" },
  { key: "uid", label: "UID" },
  { key: "vm_rss", label: "RSS (kB)" },
  { key: "vm_hwm", label: "HWM (kB)" },
  { key: "vm_size", label: "Size (kB)" },
  { key: "threads", label: "Threads" },
];

const PROC_AVG_KEYS = ["vm_rss", "vm_hwm", "vm_size", "threads"] as const;

function cellText(value: string | number | null | undefined): string {
  if (value == null || value === "") return "";
  return String(value);
}

function meanNumericTexts(values: string[]): string {
  const nums = values
    .map((v) => Number(v))
    .filter((n) => Number.isFinite(n));
  if (nums.length === 0) return "";
  const mean = nums.reduce((a, b) => a + b, 0) / nums.length;
  return formatDecimalStandard(mean);
}

type ProcTableGroup = {
  proc: string;
  hostCount: number;
  averages: Record<string, string>;
  rows: Array<Record<string, string>>;
};

function buildProcTable(procList: ProcListEntry[]): {
  columns: Array<{ key: string; label: string }>;
  groups: ProcTableGroup[];
  /** Flat rows for legacy string-only lists. */
  rows: Array<Record<string, string>>;
  legacyOnly: boolean;
} {
  const legacyOnly = procList.every((entry) => typeof entry === "string");
  if (legacyOnly) {
    return {
      columns: [{ key: "proc", label: "Process" }],
      groups: [],
      rows: procList.map((entry) => ({ proc: String(entry) })),
      legacyOnly: true,
    };
  }

  const rows = procList.map((entry) => {
    if (typeof entry === "string") {
      return { proc: entry };
    }
    const row: Record<string, string> = {};
    for (const col of PROC_TABLE_COLUMNS) {
      const text = cellText(entry[col.key]);
      if (text) row[col.key] = text;
    }
    if (!row.proc && entry.device != null && String(entry.device) !== "") {
      row.proc = String(entry.device);
    }
    return row;
  });

  const columns = PROC_TABLE_COLUMNS.filter((col) =>
    rows.some((row) => row[col.key] != null && row[col.key] !== ""),
  ).map((col) => ({ key: col.key, label: col.label }));

  if (columns.length === 0) {
    return {
      columns: [{ key: "proc", label: "Process" }],
      groups: [],
      rows: rows.map((row) => ({ proc: row.proc || "" })),
      legacyOnly: true,
    };
  }

  const byProc = new Map<string, Array<Record<string, string>>>();
  for (const row of rows) {
    const name = row.proc || "(unnamed)";
    const list = byProc.get(name) || [];
    list.push(row);
    byProc.set(name, list);
  }
  const groups: ProcTableGroup[] = Array.from(byProc.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([proc, groupRows]) => {
      const averages: Record<string, string> = {};
      for (const key of PROC_AVG_KEYS) {
        if (!columns.some((c) => c.key === key)) continue;
        averages[key] = meanNumericTexts(groupRows.map((r) => r[key] || ""));
      }
      return {
        proc,
        hostCount: groupRows.length,
        averages,
        rows: groupRows,
      };
    });

  return { columns, groups, rows: [], legacyOnly: false };
}

function resolveGpuCountForDisplay(
  gpuCount: string | number | null | undefined,
  metricsList: JobMetricDisplayRow[],
): string | number | null {
  if (gpuCount != null && gpuCount !== "" && !Number.isNaN(Number(gpuCount))) {
    return gpuCount;
  }
  const detail = metricsList.find((m) => m.metric === "detail_gpu_count");
  if (detail?.value != null && detail.value !== "" && !Number.isNaN(Number(detail.value))) {
    return detail.value;
  }
  return null;
}

function analysisTabFromSearchParams(
  searchParams: URLSearchParams | { get: (key: string) => string | null },
): JobAnalysisTab {
  const rawTab = readTabFromSearchParams(searchParams, "tab", "metrics");
  return JOB_DETAIL_ANALYSIS_TABS.has(rawTab as JobAnalysisTab)
    ? (rawTab as JobAnalysisTab)
    : "metrics";
}

function buildJobDetailTitle({
  error,
  loading,
  data,
  pk,
}: {
  error: string | null;
  loading: boolean;
  data: JobDetailViewData | null;
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
  return value ? <TextLink href={to}>{value}</TextLink> : fallbackText;
}

const PlotPanel = memo(function PlotPanel({
  item,
  id,
  plotName,
  unavailableReason,
  isLoading,
  maximizeInContainer = "width",
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
        maximizeInContainer={maximizeInContainer}
      />
    </div>
  );
});

export default function JobDetail() {
  const session = useSession();
  const isStaff = !!session?.is_staff;
  const { flatParams } = useMachineRouteParams();
  const pk = flatParams.pk ?? "";
  const rawSearchParams = useSearchParams();
  const pathname = usePathname();
  const {
    data: jobDetailData,
    error,
    initialLoading,
    detailBusy,
    detailsLoading,
    detailFetchWarning,
    loadFullDetail,
    loadDetailWithoutDeferParts,
  } = useJobDetailQuery(pk);
  const data = jobDetailData as JobDetailViewData | null;
  const [analysisTab, setAnalysisTabState] = useState<JobAnalysisTab>(() =>
    analysisTabFromSearchParams(rawSearchParams),
  );

  useEffect(() => {
    setAnalysisTabState(analysisTabFromSearchParams(rawSearchParams));
    // Re-sync tab from URL when navigating to a different job identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pk identity only
  }, [pk]);

  const plotsEnabled =
    !!pk &&
    !initialLoading &&
    !error &&
    !!data &&
    (analysisTab === "summary" || analysisTab === "roofline");
  const { plots, plotsLoading, plotsFetchFailed, retryJobPlots } = useJobPlotsQuery(
    pk,
    plotsEnabled,
  );

  useEffect(() => {
    if (!pk || initialLoading) return;
    if (analysisTab === "multiprecisionMix") {
      loadDetailWithoutDeferParts(["xalt", "proc"]);
    } else if (analysisTab === "processes" || analysisTab === "execHosts") {
      loadDetailWithoutDeferParts(["multiprecision"]);
    } else if (analysisTab === "device") {
      loadFullDetail();
    }
  }, [analysisTab, pk, initialLoading, loadFullDetail, loadDetailWithoutDeferParts]);

  function setAnalysisTab(tab: JobAnalysisTab): void {
    setAnalysisTabState(tab);
    const current =
      typeof window !== "undefined"
        ? new URLSearchParams(window.location.search)
        : new URLSearchParams(rawSearchParams.toString());
    replaceTabInHistory(
      pathname,
      current,
      "tab",
      tab === "metrics" ? null : tab,
    );
  }

  useDocumentTitle(buildJobDetailTitle({ error, loading: initialLoading, data, pk }));

  if (initialLoading) {
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
  const job: JobSummaryRow = detailData.job_data ?? {};
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
    staff_artifact_contract: staffArtifactContract,
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
        gpu_utilization_max != null && String(gpu_utilization_max) !== ""
          ? `${formatDecimalStandard(gpu_utilization_max)}%`
          : "",
    },
    {
      key: "gpu_util_mean",
      label: "Mean GPU Utilization:",
      value:
        gpu_utilization_mean != null && String(gpu_utilization_mean) !== ""
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

  const metricsListFull: JobMetricDisplayRow[] = buildMetricsDisplayList(
    (metrics_list || []) as JobMetricDisplayRow[],
  );
  const metricsSourceSections = groupJobMetricsBySourceSection(metricsListFull);
  const gpuCountForMetrics = resolveGpuCountForDisplay(gpu_count, metrics_list || []);
  const wattHoursHasGpu = jobHasGpuForWattHoursLabel(gpuCountForMetrics);
  const wattHoursMetric = (metrics_list || []).find(
    (m) => m.metric === "job_cpu_gpu_watt_hours" && m.value != null,
  );
  const procTable = buildProcTable((proc_list || []) as ProcListEntry[]);

  function metricTableRows(list: JobMetricDisplayRow[]): ReactNode {
    return list.map((obj) => (
      <TableRow key={obj.metric}>
        <TableHead scope="row">
          <VariableInfoLabel
            variableName={
              obj.metric === EFFECTIVE_VECTOR_WIDTH_METRIC
                ? "avg_vector_width_64b"
                : obj.metric
            }
            labelText={
              obj.metric === EFFECTIVE_VECTOR_WIDTH_METRIC
                ? EFFECTIVE_VECTOR_WIDTH_LABEL
                : obj.metric === "job_cpu_gpu_watt_hours"
                  ? getJobWattHoursShortLabel(wattHoursHasGpu)
                  : getJobMetricShortLabel(obj.metric)
            }
            enableHelp
            suffixBeforeHelp={
              showMetricUnitsSuffix(obj) ? (
                <span className="font-normal whitespace-nowrap text-muted-foreground">
                  [{obj.units}]
                </span>
              ) : null
            }
          />
        </TableHead>
        <TableCell className={obj.value != null && obj.value !== "" ? "" : "text-muted-foreground"}>
          {formatJobMetricCell(obj, isStaff, job.ncores, gpuCountForMetrics)}
        </TableCell>
      </TableRow>
    ));
  }

  function renderMetricsSectionTables(
    sectionId: string,
    rows: JobMetricDisplayRow[],
  ): ReactNode {
    if (rows.length === 0) {
      return <p className="text-muted-foreground mb-0">Data not available.</p>;
    }
    const left = rows.filter((_, index) => index % 2 === 0);
    const right = rows.filter((_, index) => index % 2 === 1);
    if (right.length === 0) {
      return (
        <Table className={cn(JOB_DETAIL_COMPACT_TABLE_CLASS, "job-detail-metrics-table mb-0 w-full")}>
          <TableCaption className="sr-only">
            Job-level {sectionId} metrics for job {job.jid}
          </TableCaption>
          <TableBody>{metricTableRows(left)}</TableBody>
        </Table>
      );
    }
    return (
      <div className="job-detail-metrics-two-col grid gap-3 lg:grid-cols-2">
        <div>
          <Table className={cn(JOB_DETAIL_COMPACT_TABLE_CLASS, "job-detail-metrics-table mb-0 w-full")}>
            <TableCaption className="sr-only">
              Job-level {sectionId} metrics for job {job.jid} (column 1)
            </TableCaption>
            <TableBody>{metricTableRows(left)}</TableBody>
          </Table>
        </div>
        <div>
          <Table className={cn(JOB_DETAIL_COMPACT_TABLE_CLASS, "job-detail-metrics-table mb-0 w-full")}>
            <TableCaption className="sr-only">
              Job-level {sectionId} metrics for job {job.jid} (column 2)
            </TableCaption>
            <TableBody>{metricTableRows(right)}</TableBody>
          </Table>
        </div>
      </div>
    );
  }

  function renderSinglePlotPanel(config: JobPlotConfig | undefined, isTabActive: boolean): ReactNode {
    if (!config) return null;
    const panel = plotPanels.find((p) => p.key === config.panelKey);
    if (!panel) return null;
    return (
      <div key={config.key} className="mb-3 w-full min-w-0 box-border">
        <h3 className="text-base font-medium">{config.plotName}</h3>
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
          { label: "Browse", to: "/machine/" },
          { label: `Job ${job.jid}` },
        ]}
      />
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">Job {job.jid}</h1>
      {detailBusy ? (
        <p className="mb-2 text-sm text-muted-foreground" role="status" aria-live="polite">
          Updating job detail…
        </p>
      ) : null}
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
                    `/machine/queue/${encodeURIComponent(String(job.queue ?? ""))}/`,
                    ""
                  )}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">User</div>
                <div>
                  {renderJobEntityLink(
                    job.username,
                    `/machine/username/${encodeURIComponent(String(job.username ?? ""))}/`,
                    "Unknown"
                  )}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">Project</div>
                <div>
                  {renderJobEntityLink(
                    job.account,
                    `/machine/account/${encodeURIComponent(String(job.account ?? ""))}/`,
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
                <div className="col-span-full space-y-2">
                  <div>
                    <div className="text-muted-foreground">
                      <VariableInfoLabel
                        variableName="metrics_distinct_time_count"
                        labelText="Sample Count"
                        enableHelp
                      />
                    </div>
                    <div>
                      {staffMetricsDistinctTimeCount != null &&
                      String(staffMetricsDistinctTimeCount) !== ""
                        ? formatDecimalStandard(staffMetricsDistinctTimeCount)
                        : "Not computed yet."}
                    </div>
                  </div>
                  {staffArtifactContract ? (
                    <div>
                      <div className="text-muted-foreground">
                        <VariableInfoLabel
                          variableName="staff_artifact_contract"
                          labelText="Artifact schema"
                          enableHelp
                        />
                      </div>
                      <div>
                        Current: plot {staffArtifactContract.current_plot}, detail{" "}
                        {staffArtifactContract.current_detail}
                      </div>
                      <div>
                        DB: plot {formatArtifactSchemaList(staffArtifactContract.db_plot)},
                        detail {formatArtifactSchemaList(staffArtifactContract.db_detail)}
                      </div>
                    </div>
                  ) : null}
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
                    <TextLink href={`/machine/job/${job.jid}/`}>{job.jid}</TextLink>
                  </TableCell>
                  <TableCell>
                    {renderJobEntityLink(
                      job.username,
                      `/machine/username/${encodeURIComponent(String(job.username ?? ""))}/`,
                      "Unknown"
                    )}
                  </TableCell>
                  <TableCell>
                    {renderJobEntityLink(
                      job.account,
                      `/machine/account/${encodeURIComponent(String(job.account ?? ""))}/`,
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
                      `/machine/queue/${encodeURIComponent(String(job.queue ?? ""))}/`,
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
            {wattHoursMetric ? (
              <Table className="mb-3 border text-sm">
                <TableCaption className="sr-only">
                  {wattHoursHasGpu
                    ? `CPU and GPU energy for job ${job.jid}`
                    : `CPU energy for job ${job.jid}`}
                </TableCaption>
                <TableBody>
                  <TableRow>
                    <TableCell className="border border-border">
                      <b>{getJobWattHoursResourcesTitle(wattHoursHasGpu)}</b>
                    </TableCell>
                    <TableCell className="border border-border text-right">
                      {formatDecimalStandard(wattHoursMetric.value)}
                      {wattHoursMetric.units ? ` ${wattHoursMetric.units}` : ""}
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            ) : null}
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
                        <TableCell>
                          <b>{fsioResourceLabel(key)}</b>
                        </TableCell>
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
        </div>
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
            <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
              <span>Metric help (also hover the blue ? on each subplot):</span>
              <VariableInfoLabel variableName="cpu" labelText="CPU" enableHelp />
              <VariableInfoLabel variableName="mem" labelText="Memory" enableHelp />
              <VariableInfoLabel variableName="nv_gpu_util" labelText="GPU util" enableHelp />
              <VariableInfoLabel variableName="nv_gpu_link_gbs" labelText="GPU link" enableHelp />
              <VariableInfoLabel variableName="node_power_est_w" labelText="Node power" enableHelp />
            </div>
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
            ) : (
              <div className="job-detail-metrics-sections space-y-4">
                {metricsSourceSections.map((section) => (
                  <div key={section.id} className="job-detail-metrics-section">
                    <h3 className="mb-2 text-base font-medium">{section.label}</h3>
                    {renderMetricsSectionTables(section.id, section.rows)}
                  </div>
                ))}
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
                      maximizeInContainer={false}
                    />
                  ) : null}
                </div>
              </div>
              <div>
                <div className="mb-3 w-full min-w-0 box-border">
                  <h3 className="text-base font-medium">GPU Multiprecision Mix</h3>
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
                      maximizeInContainer={false}
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
            ) : procTable.legacyOnly ? (
              <Table className="border text-sm">
                <TableCaption className="sr-only">
                  Processes recorded for job {job.jid}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    {procTable.columns.map((col) => (
                      <TableHead key={col.key} scope="col">
                        {col.label}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {procTable.rows.map((row, i) => (
                    <TableRow key={`proc-${row.proc ?? ""}-${i}`}>
                      {procTable.columns.map((col) => (
                        <TableCell key={col.key}>{row[col.key] ?? ""}</TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <div className="space-y-2">
                {procTable.groups.map((group) => (
                  <Collapsible
                    key={`proc-group-${group.proc}`}
                    className="rounded-lg border px-3 py-2"
                  >
                    <CollapsibleTrigger className="flex w-full cursor-pointer flex-wrap items-baseline gap-x-3 gap-y-1 text-left text-sm">
                      <span className="font-medium">{group.proc}</span>
                      <span className="text-muted-foreground">
                        {formatDecimalStandard(group.hostCount)} host
                        {group.hostCount === 1 ? "" : "s"}
                      </span>
                      {PROC_AVG_KEYS.map((key) =>
                        group.averages[key] ? (
                          <span key={key} className="text-muted-foreground">
                            avg{" "}
                            {key === "vm_rss"
                              ? "RSS"
                              : key === "vm_hwm"
                                ? "HWM"
                                : key === "vm_size"
                                  ? "Size"
                                  : "Threads"}
                            : {group.averages[key]}
                            {key === "threads" ? "" : " kB"}
                          </span>
                        ) : null,
                      )}
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <Table className="mt-2 border text-sm">
                        <TableCaption className="sr-only">
                          Hosts running {group.proc} for job {job.jid}
                        </TableCaption>
                        <TableHeader>
                          <TableRow>
                            {procTable.columns.map((col) => (
                              <TableHead key={col.key} scope="col">
                                {col.label}
                              </TableHead>
                            ))}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {group.rows.map((row, i) => (
                            <TableRow key={`proc-${group.proc}-${row.host ?? ""}-${i}`}>
                              {procTable.columns.map((col) => (
                                <TableCell key={col.key}>{row[col.key] ?? ""}</TableCell>
                              ))}
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </CollapsibleContent>
                  </Collapsible>
                ))}
              </div>
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
                        <TableRow key={`libset-${String(item[0])}-${String(item[1])}-${i}`}>
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
                      <TableRow key={`host-${host}-${i}`}>
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
                            <TextLink href={`/machine/job/${job.jid}/${type_name}/`}>{type_name}</TextLink>
                          </TableCell>
                          <TableCell className="text-left">
                            {Array.isArray(event)
                              ? event.map((ev, i) => (
                                  <span key={`${type_name}-${String(ev)}-${i}`}>
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
