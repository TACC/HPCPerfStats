"use client";

import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import type { MouseEvent } from "react";
import { ChevronDownIcon } from "lucide-react";
import { TextLink, textLinkClassName } from "@/components/TextLink";
import type { JobListEntry } from "@/api/generated/models/jobListEntry";
import type { JobListResponse } from "@/api/generated/models/jobListResponse";
import { useMinWidth } from "@/hooks/use-media-query";
import { useJobListQuery } from "@/hooks/use-job-list";
import { useJobListFilterOptions } from "@/hooks/use-job-list-filter-options";
import {
  JOB_LIST_HISTOGRAM_METRICS,
  useJobListHistograms,
  type MetricName,
} from "@/hooks/use-job-list-histograms";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
} from "@/components/ui/pagination";
import { cn } from "@/lib/utils";
import BannerErrorMessage from "../components/BannerErrorMessage";
import HistogramThumbnails from "../components/HistogramThumbnails";
import JobListFilterSummary from "../components/JobListFilterSummary";
import JobListHeaderFilters from "../components/JobListHeaderFilters";
import LoadingMessage from "../components/LoadingMessage";
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import { useExtendedSearchLayout } from "../context/extended-search-layout-context";
import { formatDateTime } from "../utils/formatDateTime";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { buildJobListApiParams, buildJobListHistogramApiParams } from "../utils/build-job-list-api-params";
import {
  buildJobListActiveFilterLines,
} from "../utils/job-list-filter-summary";
import { buildJobListBreadcrumbs } from "../utils/job-list-breadcrumbs";
import { resolveJobListSelectionContext } from "../utils/job-list-selection-context";
import { useSession } from "../session-context";
import { JOB_LIST_TABLE_HEADERS } from "../utils/site-field-labels";
import { tableSortAriaSort } from "../utils/table-sort-a11y";
import {
  readTabFromSearchParams,
  searchParamsWithTab,
} from "../utils/sync-tab-search-param";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { useMachineRouteParams } from "../hooks/use-machine-route-params";
import { useStableURLSearchParams } from "../hooks/use-stable-search-params";
import {
  softPresentationClick,
  softReplacePresentationParams,
} from "../utils/soft-presentation-nav";
import {
  jobListPageHumanSummary,
  jobListRouteTitleContext,
} from "../utils/job-list-route-title-context";

type SortDirection = "asc" | "desc";
type JobPerformanceBadge = {
  tone?: string | null;
  label?: string | null;
  aria_label?: string | null;
};
type JobListRow = Omit<JobListEntry, "performance"> & {
  performance?: JobPerformanceBadge | null;
};
type JobListApiResponse = Omit<JobListResponse, "job_list" | "filter_summary"> & {
  job_list?: JobListRow[];
  filter_summary?: string[];
};
type BuildJobListTitleArgs = {
  error: string | null;
  loading: boolean;
  data: Pick<JobListApiResponse, "qname"> | null;
  routeCtx: string;
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

const paginateLinkClass = cn(
  buttonVariants({ variant: "outline", size: "sm" }),
  "min-h-11 min-w-11 inline-flex items-center justify-center",
);

const paginateActiveLinkClass = cn(
  buttonVariants({ variant: "default", size: "sm" }),
  "min-h-11 min-w-11 inline-flex items-center justify-center",
);

function buildPaginationItems(
  currentPage: number,
  totalPages: number,
  pageRangeDisplayed = 5,
  marginPagesDisplayed = 1,
): Array<number | "ellipsis"> {
  if (totalPages <= 1) return [];

  const items: number[] = [];
  for (let i = 1; i <= totalPages; i += 1) {
    const nearCurrent = i >= currentPage - Math.floor(pageRangeDisplayed / 2)
      && i <= currentPage + Math.floor(pageRangeDisplayed / 2);
    const inMargin = i <= marginPagesDisplayed || i > totalPages - marginPagesDisplayed;
    if (nearCurrent || inMargin) {
      items.push(i);
    }
  }

  const withEllipsis: Array<number | "ellipsis"> = [];
  let previous: number | undefined;
  for (const pageNumber of items) {
    if (previous !== undefined && pageNumber - previous > 1) {
      withEllipsis.push("ellipsis");
    }
    withEllipsis.push(pageNumber);
    previous = pageNumber;
  }
  return withEllipsis;
}

function buildJobListTitle({ error, loading, data, routeCtx }: BuildJobListTitleArgs): string {
  if (error) return routeCtx ? `Job list · ${routeCtx}` : "Job list";
  if (loading) return routeCtx ? `Loading job list · ${routeCtx}` : "Loading job list";
  if (data?.qname) return routeCtx ? `${data.qname} · ${routeCtx}` : data.qname;
  return routeCtx ? `Job list · ${routeCtx}` : "Job list";
}

export default function JobList() {
  const session = useSession();
  const isStaff = !!session?.is_staff;
  const searchParams = useStableURLSearchParams();
  const rawSearchParams = useSearchParams();
  const { flatParams: paramsFromRoute } = useMachineRouteParams();
  const pathname = usePathname();
  const router = useRouter();
  const [histogramReloadKey, setHistogramReloadKey] = useState(0);
  const [distributionsOpen, setDistributionsOpen] = useState(true);
  const { openExtendedSearch } = useExtendedSearchLayout();
  const listViewTab = readTabFromSearchParams(rawSearchParams, "view", "jobs");
  const isLgUp = useMinWidth(992);

  const asURLSearchParams = searchParams;

  const listApiParams = useMemo(
    () => buildJobListApiParams(asURLSearchParams, paramsFromRoute),
    [asURLSearchParams, paramsFromRoute],
  );

  const histogramApiParams = useMemo(
    () => buildJobListHistogramApiParams(asURLSearchParams, paramsFromRoute),
    [asURLSearchParams, paramsFromRoute],
  );

  const {
    data,
    error,
    initialLoading,
    tableBusy,
    jobsFetching,
  } = useJobListQuery(listApiParams);

  const jobListData = data as JobListApiResponse | null;

  const { filterOptions, optionsLoading } = useJobListFilterOptions(
    listApiParams,
    !initialLoading && !!jobListData,
  );

  // Always fetch histograms while JobList is mounted (distributions default open).
  const histogramsEnabled = true;
  const { histograms, metricHistStatus, batchError, histogramsUpdating } =
    useJobListHistograms(
      histogramApiParams,
      histogramReloadKey,
      histogramsEnabled,
      jobsFetching,
    );

  function setListViewTab(tab: "jobs" | "charts") {
    const next = searchParamsWithTab(rawSearchParams, "view", tab === "jobs" ? null : tab);
    softReplacePresentationParams(router, pathname, next, searchParams);
  }

  function navigatePresentationHref(event: MouseEvent<HTMLAnchorElement>, href: string) {
    softPresentationClick(event, router, pathname, href, searchParams);
  }

  const selectionContext = useMemo(
    () => resolveJobListSelectionContext(asURLSearchParams, paramsFromRoute),
    [asURLSearchParams, paramsFromRoute],
  );

  const routeCtx = jobListRouteTitleContext(selectionContext, asURLSearchParams);
  const documentTitleSegment = buildJobListTitle({
    error,
    loading: initialLoading,
    data: jobListData,
    routeCtx,
  });
  useDocumentTitle(documentTitleSegment);

  const filterSummaryLines = useMemo(() => {
    const orderBy = asURLSearchParams.get("order_by") || jobListData?.order_by || "-end_time";
    return buildJobListActiveFilterLines(asURLSearchParams, paramsFromRoute, {
      orderBy,
      serverSummary: jobListData?.filter_summary,
    });
  }, [asURLSearchParams, paramsFromRoute, jobListData?.order_by, jobListData?.filter_summary]);

  if (error) return <BannerErrorMessage message={error} />;
  if (!initialLoading && !jobListData) return null;

  const showTableSkeleton = initialLoading && !jobListData;

  const {
    job_list = [],
    nj = 0,
    aggregates = {},
    qname = routeCtx || "Job list",
    order_by: responseOrderBy = "-end_time",
    pagination = {},
  } = jobListData ?? {};
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

  const allMetricHistsDone = JOB_LIST_HISTOGRAM_METRICS.every(
    (m) => !metricHistStatus[m]?.loading,
  );
  const histogramsFinishedLoading = allMetricHistsDone;
  const failedHistogramLabels: string[] = [];
  const labelMap: Record<MetricName, string> = {
    runtime: "Runtime",
    nhosts: "Node count",
    queue_wait: "Queue wait",
  };
  JOB_LIST_HISTOGRAM_METRICS.forEach((metric) => {
    if (metricHistStatus[metric]?.error) {
      failedHistogramLabels.push(labelMap[metric] || metric);
    }
  });
  const firstFailedMetric = JOB_LIST_HISTOGRAM_METRICS.find((m) => metricHistStatus[m]?.error);
  const histogramErrorMessage =
    batchError ||
    (firstFailedMetric ? metricHistStatus[firstFailedMetric]?.error : null);
  const pageSummary = jobListPageHumanSummary(selectionContext);

  function handleJumpToDistributions(event: MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    setDistributionsOpen(true);
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

  // Embed when the Distributions panel is open (default). Avoid zero-size embeds.
  const distributionPlotsVisible = distributionsOpen;

  // Single source of truth for the pagination control; rendered above and below
  // the job table so users can navigate from either end of long lists.
  const renderPaginationNav = (positionId: "top" | "bottom") => {
    if (!(num_pages > 1)) return null;
    const pageItems = buildPaginationItems(page, num_pages);
    return (
      <Pagination
        className="mx-0 my-3 flex w-full max-w-none flex-wrap items-center justify-start gap-2 max-md:my-2"
        aria-label={`Job list pagination (${positionId})`}
        data-testid={`job-list-pagination-${positionId}`}
      >
        {page > 1 ? (
          <PaginationLink
            href={`${pathname}?${paginationQuery(1)}`}
            size="default"
            className={paginateLinkClass}
            aria-label="First page"
            onClick={(event) =>
              navigatePresentationHref(event, `${pathname}?${paginationQuery(1)}`)
            }
          >
            First
          </PaginationLink>
        ) : (
          <span className="text-muted-foreground" aria-hidden="true">
            First
          </span>
        )}
        <PaginationContent className="flex flex-wrap gap-2">
          <PaginationItem>
            {page > 1 ? (
              <PaginationLink
                href={`${pathname}?${paginationQuery(page - 1)}`}
                className={paginateLinkClass}
                aria-label="Previous page"
                onClick={(event) =>
                  navigatePresentationHref(
                    event,
                    `${pathname}?${paginationQuery(page - 1)}`,
                  )
                }
              >
                «
              </PaginationLink>
            ) : (
              <span className={cn(paginateLinkClass, "pointer-events-none opacity-50")} aria-hidden="true">
                «
              </span>
            )}
          </PaginationItem>
          {pageItems.map((item, index) =>
            item === "ellipsis" ? (
              <PaginationItem key={`ellipsis-${positionId}-${index}`}>
                <PaginationEllipsis />
              </PaginationItem>
            ) : (
              <PaginationItem key={`page-${item}`}>
                <PaginationLink
                  href={`${pathname}?${paginationQuery(item)}`}
                  isActive={item === page}
                  className={item === page ? paginateActiveLinkClass : paginateLinkClass}
                  aria-label={`Page ${item}`}
                  onClick={(event) =>
                    navigatePresentationHref(
                      event,
                      `${pathname}?${paginationQuery(item)}`,
                    )
                  }
                >
                  {item}
                </PaginationLink>
              </PaginationItem>
            ),
          )}
          <PaginationItem>
            {page < num_pages ? (
              <PaginationLink
                href={`${pathname}?${paginationQuery(page + 1)}`}
                className={paginateLinkClass}
                aria-label="Next page"
                onClick={(event) =>
                  navigatePresentationHref(
                    event,
                    `${pathname}?${paginationQuery(page + 1)}`,
                  )
                }
              >
                »
              </PaginationLink>
            ) : (
              <span className={cn(paginateLinkClass, "pointer-events-none opacity-50")} aria-hidden="true">
                »
              </span>
            )}
          </PaginationItem>
        </PaginationContent>
        {page < num_pages ? (
          <PaginationLink
            href={`${pathname}?${paginationQuery(num_pages)}`}
            size="default"
            className={paginateLinkClass}
            aria-label="Last page"
            onClick={(event) =>
              navigatePresentationHref(
                event,
                `${pathname}?${paginationQuery(num_pages)}`,
              )
            }
          >
            Last
          </PaginationLink>
        ) : (
          <span className="text-muted-foreground" aria-hidden="true">
            Last
          </span>
        )}
      </Pagination>
    );
  };

  const breadcrumbItems = buildJobListBreadcrumbs(selectionContext, qname);

  const distributionsBody = (
    <>
      {histogramsUpdating ? (
        <p className="mb-2 text-sm text-muted-foreground" role="status" aria-live="polite">
          Updating distributions…
        </p>
      ) : null}
      <div className="text-center">
        {JOB_LIST_HISTOGRAM_METRICS.map((metric) => {
          const status = metricHistStatus[metric] || {
            loading: false,
            error: null,
          };
          const friendlyName = labelMap[metric] || metric;
          return (
            <div key={metric}>
              {status.loading && (
                <LoadingMessage message={`Loading ${friendlyName.toLowerCase()} histogram…`} />
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
            <AlertDescription className="text-sm break-words">
              <p className="mb-2">
                Some histograms could not be loaded ({failedHistogramLabels.join(", ")}).
                The job list below is unchanged.
              </p>
              {histogramErrorMessage ? (
                <p className="mb-2 whitespace-normal break-words text-amber-950 dark:text-amber-100">
                  {histogramErrorMessage}
                </p>
              ) : null}
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
          <HistogramThumbnails histograms={histograms} embedAllowed={distributionPlotsVisible} />
        ) : null}
      </div>
    </>
  );

  return (
    <>
      <PageBreadcrumbs items={breadcrumbItems} />
      {showTableSkeleton ? (
        <span className="sr-only" role="status" aria-label="Loading job list">
          Loading job list
        </span>
      ) : null}
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

      <Collapsible
        open={distributionsOpen}
        onOpenChange={setDistributionsOpen}
        className="job-list-distributions mb-4"
      >
        <section
          id="job-list-distributions"
          aria-label="Distributions for this job selection"
        >
          <CollapsibleTrigger className="flex w-full cursor-pointer items-center gap-2 text-left">
            <h2 className="text-lg font-medium">Distributions for this job selection</h2>
            <ChevronDownIcon
              className={cn(
                "ml-auto size-4 shrink-0 transition-transform",
                distributionsOpen && "rotate-180",
              )}
              aria-hidden
            />
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-3">{distributionsBody}</CollapsibleContent>
        </section>
      </Collapsible>

      <JobListHeaderFilters
        filterOptions={filterOptions}
        optionsLoading={optionsLoading}
        routeParams={paramsFromRoute}
      />

      {!isLgUp && (
        <Tabs
          value={listViewTab}
          onValueChange={(value) => setListViewTab(value as "jobs" | "charts")}
          className="sticky top-0 z-[var(--z-sticky-inpage)] mb-2 bg-background pt-1"
        >
          <TabsList
            variant="line"
            className="w-full justify-start overflow-x-auto [scrollbar-width:thin] flex-nowrap"
            aria-label="Jobs list and charts"
          >
            <TabsTrigger value="jobs">Jobs</TabsTrigger>
            <TabsTrigger value="charts">Charts</TabsTrigger>
          </TabsList>
        </Tabs>
      )}

      <div
        id="job-list-tabpanel-jobs"
        role={isLgUp ? undefined : "tabpanel"}
        hidden={!isLgUp && listViewTab !== "jobs"}
      >
      {renderPaginationNav("top")}

      <div
        className={cn(
          tableBusy && "opacity-55",
          "max-md:[&_table]:text-sm max-md:[&_td]:whitespace-nowrap max-md:[&_td]:px-1 max-md:[&_td]:py-[0.35rem] max-md:[&_th]:whitespace-nowrap max-md:[&_th]:px-1 max-md:[&_th]:py-[0.35rem]",
          "max-lg:scroll-pt-28",
        )}
        id="job-list-table"
        aria-busy={tableBusy || showTableSkeleton}
      >
        <div className="rounded-md border">
        <Table className="border-0 text-sm max-lg:[&_tbody_tr]:scroll-mt-28">
          <TableCaption className="sr-only">
            Job list for {qname}. {nj} jobs.
          </TableCaption>
          <TableHeader className="[&_th]:sticky [&_th]:top-0 [&_th]:z-[var(--z-sticky-inpage)] [&_th]:bg-background [&_th]:shadow-[0_1px_0_var(--border)] max-lg:[&_th]:top-14">
            <TableRow>
              {columns.map(({ label, field, sortable }) => (
              <TableHead key={field} scope="col" aria-sort={ariaSortForField(field, sortable)}>
                {sortable && !showTableSkeleton ? (
                  <TextLink
                    href={sortLink(field)}
                    onClick={(event) => navigatePresentationHref(event, sortLink(field))}
                  >
                    {label}
                    {sortIndicator(field)}
                  </TextLink>
                ) : (
                  label
                )}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {showTableSkeleton
            ? [1, 2, 3, 4, 5].map((i) => (
                <TableRow key={`skeleton-${i}`}>
                  <TableCell colSpan={columns.length}>
                    <Skeleton className="h-4 w-full" />
                  </TableCell>
                </TableRow>
              ))
            : null}
          {!showTableSkeleton && job_list.length === 0 ? (
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
          {!showTableSkeleton
            ? job_list.map((job) => (
            <TableRow key={job.jid}>
              <TableCell>
                <TextLink href={`/machine/job/${job.jid}/`}>{job.jid}</TextLink>
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
                  <TextLink href={`/machine/username/${encodeURIComponent(job.username)}/`}>{job.username}</TextLink>
                ) : (
                  "unknown"
                )}
              </TableCell>
              <TableCell>
                {job.account ? (
                  <TextLink href={`/machine/account/${encodeURIComponent(job.account)}/`}>{job.account}</TextLink>
                ) : (
                  "None"
                )}
              </TableCell>
              <TableCell>{formatDateTime(job.start_time)}</TableCell>
              <TableCell>{formatDateTime(job.end_time)}</TableCell>
              <TableCell>{formatDecimalStandard(job.runtime)}</TableCell>
              <TableCell>
                {job.queue ? (
                  <TextLink href={`/machine/queue/${encodeURIComponent(job.queue)}/`}>{job.queue}</TextLink>
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
            ))
            : null}
        </TableBody>
        </Table>
        </div>
      </div>

      {renderPaginationNav("bottom")}

      <p className="mt-2 mb-0 text-sm">
        <a href="#job-list-distributions" onClick={handleJumpToDistributions} className={textLinkClassName()}>
          Jump to histograms for this list
        </a>
      </p>
      </div>

      {!isLgUp ? (
        <p className="mt-2 mb-4 text-center text-sm" hidden={listViewTab !== "charts"}>
          <a href="#job-list-table" onClick={handleBackToJobTable} className={textLinkClassName()}>
            Continue to job table
          </a>
        </p>
      ) : null}
    </>
  );
}
