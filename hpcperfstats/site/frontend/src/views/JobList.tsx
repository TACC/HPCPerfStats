import { useRouter, useParams, usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";
import type { Dispatch, MouseEvent, SetStateAction } from "react";
import ReactPaginate from "react-paginate";
import { jobsHistogramsRetrieve } from "@/api/generated/jobs/jobs";
import type { JobListEntry } from "@/api/generated/models/jobListEntry";
import type { JobListData, JobListHistogramEntry, MetricHistStatusMap } from "@/types/view-models";
import { HISTOGRAM_EMBED_VERSION } from "@/api-paths";
import { useJobListQuery } from "@/hooks/use-job-list";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import BannerErrorMessage from "../components/BannerErrorMessage";
import HistogramThumbnails from "../components/HistogramThumbnails";
import JobListFilterSummary from "../components/JobListFilterSummary";
import LoadingMessage from "../components/LoadingMessage";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import { useExtendedSearchLayout } from "../context/extended-search-layout-context";
import { formatDateTime } from "../utils/formatDateTime";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { buildJobListApiParams } from "../utils/build-job-list-api-params";
import {
  buildJobListFilterSummaryLines,
  isExtendedSearchJobsRoute,
} from "../utils/job-list-filter-summary";
import { buildJobListBreadcrumbs } from "../utils/job-list-breadcrumbs";
import { normalizeJobListHistogramEntry } from "../utils/normalize-job-list-histogram-entry";
import { useSession } from "../session-context";
import { JOB_LIST_TABLE_HEADERS } from "../utils/site-field-labels";
import { tableSortAriaSort } from "../utils/table-sort-a11y";
import {
  readTabFromSearchParams,
  searchParamsWithTab,
} from "../utils/sync-tab-search-param";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { useArrowKeyTabs } from "../hooks/useArrowKeyTabs";
import {
  jobListPageHumanSummary,
  jobListRouteTitleContext,
} from "../utils/job-list-route-title-context";

type RouteParams = Record<string, string | string[] | undefined>;
type MetricName = "runtime" | "nhosts" | "queue_wait";
type SortDirection = "asc" | "desc";
type JobPerformanceBadge = {
  tone?: string | null;
  label?: string | null;
  aria_label?: string | null;
};
type JobListRow = Omit<JobListEntry, "performance"> & {
  performance?: JobPerformanceBadge | null;
};
type JobListApiResponse = Omit<JobListData, "job_list" | "filter_summary"> & {
  job_list?: JobListRow[];
  filter_summary?: string[];
  aggregates?: {
    total_node_hours?: number | null;
    queue_wait_mean_hours?: number | null;
  };
  pagination?: {
    page?: number;
    num_pages?: number;
  };
  order_by?: string;
  current_path?: string;
  qname?: string;
  nj?: number;
};
type HistogramApiEntry = Record<string, unknown> & {
  title?: string;
  metric?: string;
  plot_item_thumb?: unknown;
  plot_item_full?: unknown;
  plot_unavailable_reason?: string | null;
};
type BuildJobListTitleArgs = {
  error: string | null;
  loading: boolean;
  data: Pick<JobListApiResponse, "qname"> | null;
  routeCtx: string;
};
type LoadHistogramArgs = {
  metric: MetricName;
  params: Record<string, string>;
  setMetricHistStatus: Dispatch<SetStateAction<MetricHistStatusMap>>;
  signal?: AbortSignal;
};
type SortField =
  | "jid"
  | "sample_count"
  | "performance_sort_rank"
  | "username"
  | "account"
  | "start_time"
  | "end_time"
  | "runtime"
  | "queue"
  | "state"
  | "ncores"
  | "nhosts"
  | "node_hrs"
  | "jobname";
type ColumnDef = {
  label: string;
  field: SortField;
  sortable: boolean;
  defaultSortDirection?: SortDirection;
};
const makeColumn = (
  label: string,
  field: SortField,
  sortable: boolean,
  defaultSortDirection?: SortDirection,
): ColumnDef => ({
  label,
  field,
  sortable,
  defaultSortDirection,
});

function performanceToneToBadgeClass(tone?: string | null): string {
  if (tone === "success") return "bg-green-600 text-white hover:bg-green-600";
  if (tone === "warning") return "bg-amber-500 text-black hover:bg-amber-500";
  if (tone === "info") return "bg-sky-500 text-white hover:bg-sky-500";
  return "";
}

const listViewTabTriggerClass = (isActive: boolean) =>
  cn(
    "inline-flex items-center justify-center rounded-t-md border border-transparent px-3 py-1.5 text-sm font-medium -mb-px transition-colors",
    "hover:border-border hover:border-b-transparent",
    isActive && "border-border border-b-transparent bg-background text-foreground",
  );

const paginateLinkClass = cn(
  buttonVariants({ variant: "outline", size: "sm" }),
  "min-h-11 min-w-11 inline-flex items-center justify-center",
);

const paginateActiveLinkClass = cn(
  buttonVariants({ variant: "default", size: "sm" }),
  "min-h-11 min-w-11 inline-flex items-center justify-center",
);

function buildJobListTitle({ error, loading, data, routeCtx }: BuildJobListTitleArgs): string {
  if (error) return routeCtx ? `Job list · ${routeCtx}` : "Job list";
  if (loading) return routeCtx ? `Loading job list · ${routeCtx}` : "Loading job list";
  if (data?.qname) return routeCtx ? `${data.qname} · ${routeCtx}` : data.qname;
  return routeCtx ? `Job list · ${routeCtx}` : "Job list";
}

async function loadHistogramForMetric({
  metric,
  params,
  setMetricHistStatus,
  signal,
}: LoadHistogramArgs): Promise<JobListHistogramEntry | null> {
  setMetricHistStatus((prev) => ({
    ...prev,
    [metric]: { loading: true, error: null },
  }));
  try {
    const histParams = {
      ...params,
      group: "metric",
      metric,
      _histogram_embed_v: HISTOGRAM_EMBED_VERSION,
    };
    const metricData = (await jobsHistogramsRetrieve(
      histParams,
      undefined,
      signal,
    )) as HistogramApiEntry | null;
    if (!metricData) return null;
    setMetricHistStatus((prev) => ({
      ...prev,
      [metric]: { loading: false, error: null },
    }));
    return normalizeJobListHistogramEntry(metricData, metric);
  } catch (err) {
    const message =
      err instanceof Error
        ? err.message
        : `Failed to load ${metric} histogram for this job list.`;
    // Metric-specific failures should not break other histograms.
    // eslint-disable-next-line no-console
    console.warn(`Failed to load job list histogram for metric '${metric}':`, err);
    setMetricHistStatus((prev) => ({
      ...prev,
      [metric]: {
        loading: false,
        error: message,
      },
    }));
    return null;
  }
}

export default function JobList() {
  const session = useSession();
  const isStaff = !!session?.is_staff;
  const searchParams = useSearchParams();
  const paramsFromRoute = useParams() as RouteParams;
  const pathname = usePathname();
  const router = useRouter();
  const metricNames: MetricName[] = ["runtime", "nhosts", "queue_wait"];
  const [histograms, setHistograms] = useState<JobListHistogramEntry[] | null>(null);
  const createInitialMetricStatus = (): MetricHistStatusMap =>
    metricNames.reduce<MetricHistStatusMap>((acc, metric) => {
      acc[metric] = { loading: false, error: null };
      return acc;
    }, {});
  const [metricHistStatus, setMetricHistStatus] = useState<MetricHistStatusMap>(createInitialMetricStatus);
  const [histogramReloadKey, setHistogramReloadKey] = useState(0);
  const { openExtendedSearch } = useExtendedSearchLayout();
  const listViewTab = readTabFromSearchParams(searchParams, "view", "jobs");
  const [isLgUp, setIsLgUp] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(min-width: 992px)").matches,
  );
  const tabJobsId = useId();
  const tabChartsId = useId();
  const listViewTabButtonIds = useMemo(() => [tabJobsId, tabChartsId], [tabJobsId, tabChartsId]);
  const activeListViewTabButtonId = listViewTab === "charts" ? tabChartsId : tabJobsId;
  const handleListViewTabKeyDown = useArrowKeyTabs(
    listViewTabButtonIds,
    activeListViewTabButtonId,
    (nextTabButtonId) => {
      setListViewTab(nextTabButtonId === tabChartsId ? "charts" : "jobs");
    },
  );

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 992px)");
    function syncLg() {
      setIsLgUp(mq.matches);
    }
    syncLg();
    mq.addEventListener("change", syncLg);
    return () => mq.removeEventListener("change", syncLg);
  }, []);

  const asURLSearchParams = useMemo(
    () => new URLSearchParams(searchParams.toString()),
    [searchParams],
  );

  const listApiParams = useMemo(
    () => buildJobListApiParams(asURLSearchParams, paramsFromRoute),
    [asURLSearchParams, paramsFromRoute],
  );

  const {
    data,
    error,
    initialLoading,
    tableBusy,
  } = useJobListQuery(listApiParams);

  const jobListData = data as JobListApiResponse | null;

  function setListViewTab(tab: "jobs" | "charts") {
    const next = searchParamsWithTab(searchParams, "view", tab === "jobs" ? null : tab);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname);
  }

  useEffect(() => {
    const params = listApiParams;
    const controller = new AbortController();

    setHistograms(null);
    setMetricHistStatus(createInitialMetricStatus());

    const loadHistograms = async () => {
      const metricPromises = metricNames.map((metric) =>
        loadHistogramForMetric({
          metric,
          params,
          setMetricHistStatus,
          signal: controller.signal,
        }),
      );

      const metricResults = await Promise.all(metricPromises);
      if (controller.signal.aborted) return;
      const metricHistograms = metricResults.filter(
        (entry): entry is JobListHistogramEntry => entry != null,
      );

      setHistograms(metricHistograms);
    };

    void loadHistograms();
    return () => controller.abort();
  }, [listApiParams, histogramReloadKey]);

  const routeCtx = jobListRouteTitleContext(paramsFromRoute, asURLSearchParams);
  const documentTitleSegment = buildJobListTitle({
    error,
    loading: initialLoading,
    data: jobListData,
    routeCtx,
  });
  useDocumentTitle(documentTitleSegment);

  const filterSummaryLines = isExtendedSearchJobsRoute(pathname)
    ? buildJobListFilterSummaryLines(asURLSearchParams)
    : jobListData?.filter_summary?.length
      ? jobListData.filter_summary
      : [];

  if (initialLoading && !jobListData) {
    return (
      <div className="job-list-skeleton" aria-busy="true">
        <span className="sr-only" role="status" aria-label="Loading job list">
          Loading job list
        </span>
        <Skeleton className="mb-3 h-8 w-2/3" />
        <Skeleton className="mb-3 h-16 w-full" />
        <Table className="border text-sm">
          <TableCaption className="sr-only">Loading</TableCaption>
          <TableBody>
            {[1, 2, 3, 4, 5].map((i) => (
              <TableRow key={i}>
                <TableCell colSpan={6}>
                  <Skeleton className="h-4 w-full" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    );
  }
  if (error) return <BannerErrorMessage message={error} />;
  if (!jobListData) return null;

  const {
    job_list = [],
    nj = 0,
    aggregates = {},
    current_path,
    qname,
    order_by: responseOrderBy = "-end_time",
    pagination = {},
  } = jobListData;
  const totalNodeHours = aggregates.total_node_hours;
  const queueWaitMeanHours = aggregates.queue_wait_mean_hours;
  const page = pagination.page ?? 1;
  const num_pages = pagination.num_pages ?? 1;

  const paginationParams = buildJobListApiParams(asURLSearchParams, paramsFromRoute);

  const paginationQuery = (pageNum: number) =>
    new URLSearchParams({ ...paginationParams, page: String(pageNum) }).toString();

  const columns: ColumnDef[] = [
    makeColumn(JOB_LIST_TABLE_HEADERS.jid, "jid", true),
    ...(isStaff ? [makeColumn("Sample count", "sample_count", true, "desc")] : []),
    makeColumn(JOB_LIST_TABLE_HEADERS.performanceData, "performance_sort_rank", true, "asc"),
    makeColumn(JOB_LIST_TABLE_HEADERS.user, "username", true),
    makeColumn(JOB_LIST_TABLE_HEADERS.project, "account", true),
    makeColumn("start time", "start_time", true),
    makeColumn("end time", "end_time", true),
    makeColumn("run time (s)", "runtime", true),
    makeColumn("queue", "queue", true),
    makeColumn("status", "state", true),
    makeColumn("cores", "ncores", true),
    makeColumn("nodes", "nhosts", true),
    makeColumn("node hrs", "node_hrs", true),
    makeColumn("name", "jobname", false),
  ];
  const defaultDirByField = columns.reduce<Record<SortField, SortDirection>>((acc, column) => {
    acc[column.field] = column.defaultSortDirection || "desc";
    return acc;
  }, {} as Record<SortField, SortDirection>);

  // Sort: all columns except name. order_by from URL/response: e.g. "-end_time" (desc) or "username" (asc).
  const orderBy = searchParams.get("order_by") || responseOrderBy;
  const sortColumn = orderBy.startsWith("-") ? orderBy.slice(1) : orderBy;
  const sortDirection = orderBy.startsWith("-") ? "desc" : "asc";
  const sortQuery = (orderByValue: string) =>
    new URLSearchParams({ ...paginationParams, order_by: orderByValue, page: "1" }).toString();
  // First-click direction is the column's natural default. performance_sort_rank
  // sorts ascending so rank 0 ("Summary available") appears first; numeric/time
  // columns default to descending so largest/newest values come first.
  const sortLink = (field: SortField) => {
    const defaultDir = defaultDirByField[field] || "desc";
    const next =
      sortColumn === field
        ? sortDirection === "desc"
          ? field
          : `-${field}`
        : defaultDir === "asc"
          ? field
          : `-${field}`;
    return `${pathname}?${sortQuery(next)}`;
  };
  const sortIndicator = (field: SortField) => {
    if (sortColumn !== field) return "";
    return sortDirection === "asc" ? " \u2191" : " \u2193";
  };

  const ariaSortForField = (field: SortField, sortable: boolean) => {
    if (!sortable) return undefined;
    return tableSortAriaSort(field, sortColumn, sortDirection) ?? "none";
  };

  const allMetricHistsDone = metricNames.every(
    (m) => !metricHistStatus[m]?.loading,
  );
  const histogramsFinishedLoading = allMetricHistsDone;
  const failedHistogramLabels: string[] = [];
  const labelMap: Record<MetricName, string> = {
    runtime: "Runtime",
    nhosts: "Node count",
    queue_wait: "Queue wait",
  };
  metricNames.forEach((metric) => {
    if (metricHistStatus[metric]?.error) {
      failedHistogramLabels.push(labelMap[metric] || metric);
    }
  });

  const pageSummary = jobListPageHumanSummary(paramsFromRoute);

  function handleJumpToDistributions(event: MouseEvent<HTMLAnchorElement>) {
    if (isLgUp) return;
    event.preventDefault();
    setListViewTab("charts");
    window.requestAnimationFrame(() => {
      document.getElementById("job-list-distributions")?.scrollIntoView({ block: "start" });
    });
  }

  function handleBackToJobTable(event: MouseEvent<HTMLAnchorElement>) {
    if (isLgUp) return;
    event.preventDefault();
    setListViewTab("jobs");
    window.requestAnimationFrame(() => {
      document.getElementById("job-list-table")?.scrollIntoView({ block: "start" });
    });
  }

  // Narrow viewports hide this section with the HTML `hidden` attribute while the
  // Jobs tab is active; Bokeh must not embed into a zero-size subtree (see BokehEmbed).
  const distributionPlotsVisible = isLgUp || listViewTab === "charts";

  // Single source of truth for the pagination control; rendered above and below
  // the job table so users can navigate from either end of long lists.
  const renderPaginationNav = (positionId: "top" | "bottom") => {
    if (!(num_pages > 1)) return null;
    return (
      <nav
        className="pagination-wrapper"
        aria-label={`Job list pagination (${positionId})`}
        data-testid={`job-list-pagination-${positionId}`}
      >
        {page > 1 ? (
          <Link
            href={`${pathname}?${paginationQuery(1)}`}
            className={cn(paginateLinkClass, "pagination-first")}
          >
            First
          </Link>
        ) : (
          <span className="pagination-first disabled text-muted-foreground" aria-hidden="true">
            First
          </span>
        )}
        <ReactPaginate
          forcePage={page - 1}
          pageCount={num_pages}
          onPageChange={({ selected }: { selected: number }) =>
            router.push(`${pathname}?${paginationQuery(selected + 1)}`)
          }
          previousLabel="«"
          nextLabel="»"
          previousAriaLabel="Previous page"
          nextAriaLabel="Next page"
          breakLabel="..."
          pageRangeDisplayed={5}
          marginPagesDisplayed={1}
          containerClassName="pagination flex flex-wrap gap-2"
          pageClassName="page-item"
          pageLinkClassName={paginateLinkClass}
          previousClassName="page-item"
          previousLinkClassName={paginateLinkClass}
          nextClassName="page-item"
          nextLinkClassName={paginateLinkClass}
          breakClassName="page-item"
          breakLinkClassName={paginateLinkClass}
          activeClassName="active"
          activeLinkClassName={paginateActiveLinkClass}
          disabledClassName="disabled"
          renderOnZeroPageCount={null}
        />
        {page < num_pages ? (
          <Link
            href={`${pathname}?${paginationQuery(num_pages)}`}
            className={cn(paginateLinkClass, "pagination-last")}
          >
            Last
          </Link>
        ) : (
          <span className="pagination-last disabled text-muted-foreground" aria-hidden="true">
            Last
          </span>
        )}
      </nav>
    );
  };

  const breadcrumbItems = buildJobListBreadcrumbs(paramsFromRoute, qname);

  return (
    <>
      <PageBreadcrumbs items={breadcrumbItems} />
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">{qname}</h1>
      {pageSummary ? (
        <p className="job-list-page-summary mb-2 text-sm text-muted-foreground">{pageSummary}</p>
      ) : null}
      <JobListFilterSummary lines={filterSummaryLines} />
      <h2 className="mb-1 text-lg font-medium">{JOB_LIST_TABLE_HEADERS.jobCount} = {nj}</h2>
      {tableBusy ? (
        <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
          Updating job list…
        </p>
      ) : null}
      {totalNodeHours != null && (
        <p className="mb-3 text-sm text-muted-foreground">
          Total node hours (all matching jobs): {formatDecimalStandard(totalNodeHours)}
        </p>
      )}
      {isStaff && queueWaitMeanHours != null ? (
        <p className="mb-3 text-sm text-muted-foreground">
          Mean queue wait (all matching jobs): {formatDecimalStandard(queueWaitMeanHours)} hours
        </p>
      ) : null}

      <section
        id="job-list-distributions"
        role={isLgUp ? undefined : "tabpanel"}
        aria-labelledby={isLgUp ? undefined : tabChartsId}
        className="job-list-distributions mb-4"
        aria-label="Distributions for this job selection"
        hidden={!isLgUp && listViewTab !== "charts"}
      >
        <h2 className="mb-2 text-lg font-medium">Distributions for this job selection</h2>
        <div className="text-center">
          {metricNames.map((metric) => {
            const status = metricHistStatus[metric] || {
              loading: false,
              error: null,
            };
            const friendlyName = labelMap[metric] || metric;
            return (
              <div key={metric}>
                {status.loading && (
                  <LoadingMessage
                    message={`Loading ${friendlyName.toLowerCase()} histogram…`}
                  />
                )}
              </div>
            );
          })}
          {histogramsFinishedLoading && failedHistogramLabels.length > 0 ? (
            <Alert
              className="mx-auto mt-2 max-w-[520px] border-amber-200 bg-amber-50 text-left text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
              role="status"
              aria-live="polite"
            >
              <AlertDescription className="text-sm">
                <p className="mb-2">
                  Some histograms could not be loaded ({failedHistogramLabels.join(", ")}).
                  The job list below is unchanged.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setHistogramReloadKey((k) => k + 1)}
                >
                  Retry histograms
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          {distributionPlotsVisible && histogramsFinishedLoading && histograms?.length === 0 ? (
            <p className="text-sm text-muted-foreground">No distribution data for this selection.</p>
          ) : null}
          {distributionPlotsVisible ? (
            <HistogramThumbnails histograms={histograms} />
          ) : null}
        </div>
        <p className="mt-2 mb-0 text-center text-sm">
          <a href="#job-list-table" onClick={handleBackToJobTable} className="text-primary hover:underline">
            Continue to job table
          </a>
        </p>
      </section>

      {!isLgUp && (
        <div className="job-list-view-tabs mb-2">
          <div
            className="job-detail-tab-scroll flex border-b"
            role="tablist"
            aria-label="Jobs list and charts"
          >
            <button
              type="button"
              id={tabJobsId}
              role="tab"
              className={listViewTabTriggerClass(listViewTab === "jobs")}
              aria-selected={listViewTab === "jobs"}
              aria-controls="job-list-tabpanel-jobs"
              tabIndex={listViewTab === "jobs" ? 0 : -1}
              onClick={() => setListViewTab("jobs")}
              onKeyDown={(e) => handleListViewTabKeyDown(e, tabJobsId)}
            >
              Jobs
            </button>
            <button
              type="button"
              id={tabChartsId}
              role="tab"
              className={listViewTabTriggerClass(listViewTab === "charts")}
              aria-selected={listViewTab === "charts"}
              aria-controls="job-list-distributions"
              tabIndex={listViewTab === "charts" ? 0 : -1}
              onClick={() => setListViewTab("charts")}
              onKeyDown={(e) => handleListViewTabKeyDown(e, tabChartsId)}
            >
              Charts
            </button>
          </div>
        </div>
      )}

      <div
        id="job-list-tabpanel-jobs"
        role={isLgUp ? undefined : "tabpanel"}
        aria-labelledby={isLgUp ? undefined : tabJobsId}
        hidden={!isLgUp && listViewTab !== "jobs"}
      >
      {renderPaginationNav("top")}

      <div
        className={cn(
          "job-list-table-wrapper",
          tableBusy && "job-list-table-busy",
        )}
        id="job-list-table"
        aria-busy={tableBusy}
      >
        <Table className="border text-sm">
          <TableCaption className="sr-only">
            Job list for {qname}. {nj} jobs.
          </TableCaption>
          <TableHeader>
            <TableRow>
              {columns.map(({ label, field, sortable }) => (
              <TableHead key={field} scope="col" aria-sort={ariaSortForField(field, sortable)}>
                {sortable ? (
                  <Link href={sortLink(field)} className="text-primary hover:underline">
                    {label}
                    {sortIndicator(field)}
                  </Link>
                ) : (
                  label
                )}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {job_list.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} className="py-4 text-center text-muted-foreground">
                No jobs match these filters.{" "}
                {filterSummaryLines.length > 0 ? (
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    className="h-auto p-0 align-baseline"
                    onClick={openExtendedSearch}
                  >
                    Modify search
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          ) : null}
          {job_list.map((job) => (
            <TableRow key={job.jid}>
              <TableCell>
                <Link href={`/machine/job/${job.jid}/`} className="text-primary hover:underline">{job.jid}</Link>
              </TableCell>
              {isStaff ? (
                <TableCell>{formatDecimalStandard(job.sample_count)}</TableCell>
              ) : null}
              <TableCell>
                {job.performance ? (
                  <Badge
                    className={performanceToneToBadgeClass(job.performance.tone)}
                    aria-label={
                      job.performance.aria_label || job.performance.label || "Performance status"
                    }
                  >
                    {job.performance.label}
                  </Badge>
                ) : (
                  <Badge variant="secondary" aria-label="Performance status unknown">
                    —
                  </Badge>
                )}
              </TableCell>
              <TableCell>
                {job.username ? (
                  <Link href={`/machine/username/${encodeURIComponent(job.username)}/`} className="text-primary hover:underline">{job.username}</Link>
                ) : (
                  "unknown"
                )}
              </TableCell>
              <TableCell>
                {job.account ? (
                  <Link href={`/machine/account/${encodeURIComponent(job.account)}/`} className="text-primary hover:underline">{job.account}</Link>
                ) : (
                  "None"
                )}
              </TableCell>
              <TableCell>{formatDateTime(job.start_time)}</TableCell>
              <TableCell>{formatDateTime(job.end_time)}</TableCell>
              <TableCell>{formatDecimalStandard(job.runtime)}</TableCell>
              <TableCell>
                {job.queue ? (
                  <Link href={`/machine/queue/${encodeURIComponent(job.queue)}/`} className="text-primary hover:underline">{job.queue}</Link>
                ) : (
                  ""
                )}
              </TableCell>
              <TableCell>{job.state}</TableCell>
              <TableCell>{formatDecimalStandard(job.ncores)}</TableCell>
              <TableCell>{formatDecimalStandard(job.nhosts)}</TableCell>
              <TableCell>{formatDecimalStandard(job.node_hrs)}</TableCell>
              <TableCell>{job.jobname}</TableCell>
            </TableRow>
          ))}
        </TableBody>
        </Table>
      </div>

      {renderPaginationNav("bottom")}

      <p className="mt-2 mb-0 text-sm">
        <a href="#job-list-distributions" onClick={handleJumpToDistributions} className="text-primary hover:underline">
          Jump to histograms for this list
        </a>
      </p>
      </div>
    </>
  );
}
