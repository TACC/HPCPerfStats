import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams, useParams, useLocation, Link, useNavigate } from "react-router-dom";
import ReactPaginate from "react-paginate";
import { api } from "../api";
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

const ResolvedReactPaginate = ReactPaginate?.default || ReactPaginate;

function performanceToneToBadgeClass(tone) {
  if (tone === "success") return "badge text-bg-success";
  if (tone === "warning") return "badge text-bg-warning";
  if (tone === "info") return "badge text-bg-info";
  return "badge text-bg-secondary";
}

function buildJobListTitle({ error, loading, data, routeCtx }) {
  if (error) return routeCtx ? `Job list · ${routeCtx}` : "Job list";
  if (loading) return routeCtx ? `Loading job list · ${routeCtx}` : "Loading job list";
  if (data?.qname) return routeCtx ? `${data.qname} · ${routeCtx}` : data.qname;
  return routeCtx ? `Job list · ${routeCtx}` : "Job list";
}

async function loadHistogramForMetric({ metric, params, setMetricHistStatus }) {
  setMetricHistStatus((prev) => ({
    ...prev,
    [metric]: { loading: true, error: null },
  }));
  try {
    const metricData = await api.getJobMetricHistogram(params, metric);
    if (!metricData) return null;
    setMetricHistStatus((prev) => ({
      ...prev,
      [metric]: { loading: false, error: null },
    }));
    return normalizeJobListHistogramEntry(metricData, metric);
  } catch (err) {
    // Metric-specific failures should not break other histograms.
    // eslint-disable-next-line no-console
    console.warn(`Failed to load job list histogram for metric '${metric}':`, err);
    setMetricHistStatus((prev) => ({
      ...prev,
      [metric]: {
        loading: false,
        error: err?.message || `Failed to load ${metric} histogram for this job list.`,
      },
    }));
    return null;
  }
}

export default function JobList() {
  const session = useSession();
  const isStaff = !!session?.is_staff;
  const [searchParams] = useSearchParams();
  const paramsFromRoute = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [histograms, setHistograms] = useState(null);
  const metricNames = ["runtime", "nhosts", "queue_wait"];
  const createInitialMetricStatus = () =>
    metricNames.reduce(
      (acc, metric) => ({
        ...acc,
        [metric]: { loading: false, error: null },
      }),
      {}
    );
  const [metricHistStatus, setMetricHistStatus] = useState(
    createInitialMetricStatus
  );
  const [error, setError] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [tableBusy, setTableBusy] = useState(false);
  const [histogramReloadKey, setHistogramReloadKey] = useState(0);
  const prevSearchKeyRef = useRef("");
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

  function setListViewTab(tab) {
    const next = searchParamsWithTab(searchParams, "view", tab === "jobs" ? null : tab);
    const qs = next.toString();
    navigate(qs ? `${location.pathname}?${qs}` : location.pathname, { replace: true });
  }

  useEffect(() => {
    const params = buildJobListApiParams(searchParams, paramsFromRoute);
    const curr = new URLSearchParams(searchParams);
    const prev = new URLSearchParams(prevSearchKeyRef.current);
    curr.delete("page");
    curr.delete("view");
    prev.delete("page");
    prev.delete("view");
    const pageOnly =
      prevSearchKeyRef.current !== "" &&
      curr.toString() === prev.toString() &&
      data != null;
    prevSearchKeyRef.current = searchParams.toString();

    if (pageOnly) {
      setTableBusy(true);
    } else {
      setInitialLoading(true);
      setData(null);
    }
    setError(null);
    // Load job list first so the table renders quickly
    api
      .getJobList(params)
      .then((listData) => {
        setData(listData);
      })
      .catch((e) => setError(e.message))
      .finally(() => {
        setInitialLoading(false);
        setTableBusy(false);
      });

    // Then load histograms separately so they don't block the list
    setHistograms(null);
    setMetricHistStatus(createInitialMetricStatus());

    const loadHistograms = async () => {
      const metricPromises = metricNames.map((metric) =>
        loadHistogramForMetric({ metric, params, setMetricHistStatus })
      );

      const metricResults = await Promise.all(metricPromises);
      const metricHistograms = metricResults.filter(Boolean);

      setHistograms(metricHistograms);
    };

    loadHistograms();
  }, [searchParams, paramsFromRoute, histogramReloadKey]);

  const routeCtx = jobListRouteTitleContext(paramsFromRoute, searchParams);
  const documentTitleSegment = buildJobListTitle({
    error,
    loading: initialLoading,
    data,
    routeCtx,
  });
  useDocumentTitle(documentTitleSegment);

  const filterSummaryLines = isExtendedSearchJobsRoute(location.pathname)
    ? buildJobListFilterSummaryLines(searchParams)
    : data?.filter_summary?.length
      ? data.filter_summary
      : [];

  if (initialLoading && !data) {
    return (
      <div className="job-list-skeleton" aria-busy="true">
        <span className="visually-hidden" role="status" aria-label="Loading job list">
          Loading job list
        </span>
        <div className="placeholder-glow mb-3">
          <span className="placeholder col-8 col-md-6" style={{ height: "2rem" }} />
        </div>
        <div className="placeholder-glow mb-3">
          <span className="placeholder col-12" style={{ height: "4rem" }} />
        </div>
        <div className="table-responsive">
          <table className="table table-sm table-bordered">
            <caption className="visually-hidden">Loading</caption>
            <tbody>
              {[1, 2, 3, 4, 5].map((i) => (
                <tr key={i}>
                  <td colSpan={6}>
                    <span className="placeholder col-12" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  if (error) return <BannerErrorMessage message={error} />;
  if (!data) return null;

  const {
    job_list = [],
    nj = 0,
    aggregates = {},
    current_path,
    qname,
    order_by: responseOrderBy = "-end_time",
    pagination = {},
  } = data;
  const totalNodeHours = aggregates.total_node_hours;
  const queueWaitMeanHours = aggregates.queue_wait_mean_hours;
  const { page, num_pages } = pagination;

  const paginationParams = buildJobListApiParams(searchParams, paramsFromRoute);

  const paginationQuery = (pageNum) =>
    new URLSearchParams({ ...paginationParams, page: String(pageNum) }).toString();

  const columns = [
    { label: JOB_LIST_TABLE_HEADERS.jid, field: "jid", sortable: true },
    ...(isStaff
      ? [{ label: "Sample count", field: "sample_count", sortable: true, defaultSortDirection: "desc" }]
      : []),
    {
      label: JOB_LIST_TABLE_HEADERS.performanceData,
      field: "performance_sort_rank",
      sortable: true,
      defaultSortDirection: "asc",
    },
    { label: JOB_LIST_TABLE_HEADERS.user, field: "username", sortable: true },
    { label: JOB_LIST_TABLE_HEADERS.project, field: "account", sortable: true },
    { label: "start time", field: "start_time", sortable: true },
    { label: "end time", field: "end_time", sortable: true },
    { label: "run time (s)", field: "runtime", sortable: true },
    { label: "queue", field: "queue", sortable: true },
    { label: "status", field: "state", sortable: true },
    { label: "cores", field: "ncores", sortable: true },
    { label: "nodes", field: "nhosts", sortable: true },
    { label: "node hrs", field: "node_hrs", sortable: true },
    { label: "name", field: "jobname", sortable: false },
  ];
  const defaultDirByField = Object.fromEntries(
    columns.map((c) => [c.field, c.defaultSortDirection || "desc"]),
  );

  // Sort: all columns except name. order_by from URL/response: e.g. "-end_time" (desc) or "username" (asc).
  const orderBy = searchParams.get("order_by") || responseOrderBy;
  const sortColumn = orderBy.startsWith("-") ? orderBy.slice(1) : orderBy;
  const sortDirection = orderBy.startsWith("-") ? "desc" : "asc";
  const sortQuery = (orderByValue) =>
    new URLSearchParams({ ...paginationParams, order_by: orderByValue, page: "1" }).toString();
  // First-click direction is the column's natural default. performance_sort_rank
  // sorts ascending so rank 0 ("Summary available") appears first; numeric/time
  // columns default to descending so largest/newest values come first.
  const sortLink = (field) => {
    const defaultDir = defaultDirByField[field] || "desc";
    const next =
      sortColumn === field
        ? sortDirection === "desc"
          ? field
          : `-${field}`
        : defaultDir === "asc"
          ? field
          : `-${field}`;
    return `${location.pathname}?${sortQuery(next)}`;
  };
  const sortIndicator = (field) => {
    if (sortColumn !== field) return "";
    return sortDirection === "asc" ? " \u2191" : " \u2193";
  };

  const ariaSortForField = (field, sortable) => {
    if (!sortable) return undefined;
    return tableSortAriaSort(field, sortColumn, sortDirection) ?? "none";
  };

  const allMetricHistsDone = metricNames.every(
    (m) => !metricHistStatus[m]?.loading,
  );
  const histogramsFinishedLoading = allMetricHistsDone;
  const failedHistogramLabels = [];
  const labelMap = {
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

  function handleJumpToDistributions(event) {
    if (isLgUp) return;
    event.preventDefault();
    setListViewTab("charts");
    window.requestAnimationFrame(() => {
      document.getElementById("job-list-distributions")?.scrollIntoView({ block: "start" });
    });
  }

  function handleBackToJobTable(event) {
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
  const renderPaginationNav = (positionId) => {
    if (!(num_pages > 1)) return null;
    return (
      <nav
        className="pagination-wrapper"
        aria-label={`Job list pagination (${positionId})`}
        data-testid={`job-list-pagination-${positionId}`}
      >
        {page > 1 ? (
          <Link
            to={`${location.pathname}?${paginationQuery(1)}`}
            className="pagination-first"
          >
            First
          </Link>
        ) : (
          <span className="pagination-first disabled" aria-hidden="true">
            First
          </span>
        )}
        <ResolvedReactPaginate
          forcePage={page - 1}
          pageCount={num_pages}
          onPageChange={({ selected }) =>
            navigate(`${location.pathname}?${paginationQuery(selected + 1)}`)
          }
          previousLabel="«"
          nextLabel="»"
          previousAriaLabel="Previous page"
          nextAriaLabel="Next page"
          breakLabel="..."
          pageRangeDisplayed={5}
          marginPagesDisplayed={1}
          containerClassName="pagination"
          pageClassName="page-item"
          pageLinkClassName="page-link"
          previousClassName="page-item"
          previousLinkClassName="page-link"
          nextClassName="page-item"
          nextLinkClassName="page-link"
          breakClassName="page-item"
          breakLinkClassName="page-link"
          activeClassName="active"
          disabledClassName="disabled"
          renderOnZeroPageCount={null}
          ariaLabelBuilder={(pageNumber, selected) =>
            selected ? `Current page, page ${pageNumber}` : `Go to page ${pageNumber}`
          }
        />
        {page < num_pages ? (
          <Link
            to={`${location.pathname}?${paginationQuery(num_pages)}`}
            className="pagination-last"
          >
            Last
          </Link>
        ) : (
          <span className="pagination-last disabled" aria-hidden="true">
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
      <h1 className="h2 mb-3">{qname}</h1>
      {pageSummary ? (
        <p className="text-muted small mb-2 job-list-page-summary">{pageSummary}</p>
      ) : null}
      <JobListFilterSummary lines={filterSummaryLines} />
      <h2 className="h5 mb-1">{JOB_LIST_TABLE_HEADERS.jobCount} = {nj}</h2>
      {tableBusy ? (
        <p className="text-muted small" role="status" aria-live="polite">
          Updating job list…
        </p>
      ) : null}
      {totalNodeHours != null && (
        <p className="mb-3 text-muted small">
          Total node hours (all matching jobs): {formatDecimalStandard(totalNodeHours)}
        </p>
      )}
      {isStaff && queueWaitMeanHours != null ? (
        <p className="mb-3 text-muted small">
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
        <h2 className="h5 mb-2">Distributions for this job selection</h2>
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
            <div
              className="alert alert-warning small mt-2 mx-auto text-start"
              style={{ maxWidth: 520 }}
              role="status"
              aria-live="polite"
            >
              <p className="mb-2">
                Some histograms could not be loaded ({failedHistogramLabels.join(", ")}).
                The job list below is unchanged.
              </p>
              <button
                type="button"
                className="btn btn-outline-secondary btn-sm"
                onClick={() => setHistogramReloadKey((k) => k + 1)}
              >
                Retry histograms
              </button>
            </div>
          ) : null}
          {distributionPlotsVisible && histogramsFinishedLoading && histograms?.length === 0 ? (
            <p className="text-muted small">No distribution data for this selection.</p>
          ) : null}
          {distributionPlotsVisible ? (
            <HistogramThumbnails histograms={histograms} />
          ) : null}
        </div>
        <p className="small text-center mt-2 mb-0">
          <a href="#job-list-table" onClick={handleBackToJobTable}>
            Continue to job table
          </a>
        </p>
      </section>

      {!isLgUp && (
        <div className="job-list-view-tabs mb-2">
          <ul
            className="nav nav-tabs job-detail-tab-scroll"
            role="tablist"
            aria-label="Jobs list and charts"
          >
            <li className="nav-item" role="presentation">
              <button
                type="button"
                id={tabJobsId}
                role="tab"
                className={`nav-link ${listViewTab === "jobs" ? "active" : ""}`}
                aria-selected={listViewTab === "jobs"}
                aria-controls="job-list-tabpanel-jobs"
                tabIndex={listViewTab === "jobs" ? 0 : -1}
                onClick={() => setListViewTab("jobs")}
                onKeyDown={(e) => handleListViewTabKeyDown(e, tabJobsId)}
              >
                Jobs
              </button>
            </li>
            <li className="nav-item" role="presentation">
              <button
                type="button"
                id={tabChartsId}
                role="tab"
                className={`nav-link ${listViewTab === "charts" ? "active" : ""}`}
                aria-selected={listViewTab === "charts"}
                aria-controls="job-list-distributions"
                tabIndex={listViewTab === "charts" ? 0 : -1}
                onClick={() => setListViewTab("charts")}
                onKeyDown={(e) => handleListViewTabKeyDown(e, tabChartsId)}
              >
                Charts
              </button>
            </li>
          </ul>
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
        className={`table-responsive job-list-table-wrapper${tableBusy ? " job-list-table-busy" : ""}`}
        id="job-list-table"
        aria-busy={tableBusy}
      >
        <table className="table table-sm table-bordered">
          <caption className="visually-hidden">
            Job list for {qname}. {nj} jobs.
          </caption>
          <thead>
            <tr>
              {columns.map(({ label, field, sortable }) => (
              <th key={field} scope="col" aria-sort={ariaSortForField(field, sortable)}>
                {sortable ? (
                  <Link to={sortLink(field)}>
                    {label}
                    {sortIndicator(field)}
                  </Link>
                ) : (
                  label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {job_list.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="text-center text-muted py-4">
                No jobs match these filters.{" "}
                {filterSummaryLines.length > 0 ? (
                  <button
                    type="button"
                    className="btn btn-link btn-sm p-0 align-baseline"
                    onClick={openExtendedSearch}
                  >
                    Modify search
                  </button>
                ) : null}
              </td>
            </tr>
          ) : null}
          {job_list.map((job) => (
            <tr key={job.jid}>
              <td>
                <Link to={`/job/${job.jid}`}>{job.jid}</Link>
              </td>
              {isStaff ? (
                <td>{formatDecimalStandard(job.sample_count)}</td>
              ) : null}
              <td>
                {job.performance ? (
                  <span
                    className={performanceToneToBadgeClass(job.performance.tone)}
                    aria-label={
                      job.performance.aria_label || job.performance.label || "Performance status"
                    }
                  >
                    {job.performance.label}
                  </span>
                ) : (
                  <span className="badge text-bg-secondary" aria-label="Performance status unknown">
                    —
                  </span>
                )}
              </td>
              <td>
                {job.username ? (
                  <Link to={`/username/${encodeURIComponent(job.username)}`}>{job.username}</Link>
                ) : (
                  "unknown"
                )}
              </td>
              <td>
                {job.account ? (
                  <Link to={`/account/${encodeURIComponent(job.account)}`}>{job.account}</Link>
                ) : (
                  "None"
                )}
              </td>
              <td>{formatDateTime(job.start_time)}</td>
              <td>{formatDateTime(job.end_time)}</td>
              <td>{formatDecimalStandard(job.runtime)}</td>
              <td>
                {job.queue ? (
                  <Link to={`/queue/${encodeURIComponent(job.queue)}`}>{job.queue}</Link>
                ) : (
                  ""
                )}
              </td>
              <td>{job.state}</td>
              <td>{formatDecimalStandard(job.ncores)}</td>
              <td>{formatDecimalStandard(job.nhosts)}</td>
              <td>{formatDecimalStandard(job.node_hrs)}</td>
              <td>{job.jobname}</td>
            </tr>
          ))}
        </tbody>
        </table>
      </div>

      {renderPaginationNav("bottom")}

      <p className="small mt-2 mb-0">
        <a href="#job-list-distributions" onClick={handleJumpToDistributions}>
          Jump to histograms for this list
        </a>
      </p>
      </div>
    </>
  );
}
