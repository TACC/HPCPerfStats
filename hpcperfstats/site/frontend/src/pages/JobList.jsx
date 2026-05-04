import { useEffect, useId, useState } from "react";
import { useSearchParams, useParams, useLocation, Link, useNavigate } from "react-router-dom";
import ReactPaginate from "react-paginate";
import { api } from "../api";
import BannerErrorMessage from "../components/BannerErrorMessage";
import HistogramThumbnails from "../components/HistogramThumbnails";
import LoadingMessage from "../components/LoadingMessage";
import { formatDateTime } from "../utils/formatDateTime";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { buildJobListApiParams } from "../utils/build-job-list-api-params";
import { normalizeJobListHistogramEntry } from "../utils/normalize-job-list-histogram-entry";
import { useSession } from "../session-context";
import { tableSortAriaSort } from "../utils/table-sort-a11y";
import { useDocumentTitle } from "../utils/useDocumentTitle";
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
  const [loading, setLoading] = useState(true);
  const [histogramReloadKey, setHistogramReloadKey] = useState(0);
  const [listViewTab, setListViewTab] = useState("jobs");
  const [isLgUp, setIsLgUp] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(min-width: 992px)").matches,
  );
  const tabJobsId = useId();
  const tabChartsId = useId();

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 992px)");
    function syncLg() {
      setIsLgUp(mq.matches);
    }
    syncLg();
    mq.addEventListener("change", syncLg);
    return () => mq.removeEventListener("change", syncLg);
  }, []);

  useEffect(() => {
    const params = buildJobListApiParams(searchParams, paramsFromRoute);
    setLoading(true);
    setError(null);
    setData(null);
    // Load job list first so the table renders quickly
    api
      .getJobList(params)
      .then((listData) => {
        setData(listData);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

    // Then load histograms separately so they don't block the list
    setHistograms(null);
    setMetricHistStatus(createInitialMetricStatus());

    const loadHistograms = async () => {
      const metricPromises = metricNames.map((metric) => {
        return api
          .getJobMetricHistogram(params, metric)
          .then((metricData) => {
            if (!metricData) {
              return null;
            }
            setMetricHistStatus((prev) => ({
              ...prev,
              [metric]: { loading: false, error: null },
            }));
            return normalizeJobListHistogramEntry(metricData, metric);
          })
          .catch((err) => {
            // Metric-specific failures should not break other histograms.
            // eslint-disable-next-line no-console
            console.warn(
              `Failed to load job list histogram for metric '${metric}':`,
              err
            );
            setMetricHistStatus((prev) => ({
              ...prev,
              [metric]: {
                loading: false,
                error:
                  err?.message ||
                  `Failed to load ${metric} histogram for this job list.`,
              },
            }));
            return null;
          });
      });

      const metricResults = await Promise.all(metricPromises);
      const metricHistograms = metricResults.filter(Boolean);

      setHistograms(metricHistograms);
    };

    loadHistograms();
  }, [searchParams, paramsFromRoute, histogramReloadKey]);

  const routeCtx = jobListRouteTitleContext(paramsFromRoute, searchParams);
  const documentTitleSegment = error
    ? routeCtx
      ? `Job list · ${routeCtx}`
      : "Job list"
    : loading
      ? routeCtx
        ? `Loading job list · ${routeCtx}`
        : "Loading job list"
      : data?.qname
        ? routeCtx
          ? `${data.qname} · ${routeCtx}`
          : data.qname
        : routeCtx
          ? `Job list · ${routeCtx}`
          : "Job list";
  useDocumentTitle(documentTitleSegment);

  if (loading) {
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
  const queueWaitStddevHours = aggregates.queue_wait_stddev_hours;
  const { page, num_pages } = pagination;

  const paginationParams = buildJobListApiParams(searchParams, paramsFromRoute);

  const paginationQuery = (pageNum) =>
    new URLSearchParams({ ...paginationParams, page: String(pageNum) }).toString();

  // Sort: all columns except name. order_by from URL/response: e.g. "-end_time" (desc) or "username" (asc).
  const orderBy = searchParams.get("order_by") || responseOrderBy;
  const sortColumn = orderBy.startsWith("-") ? orderBy.slice(1) : orderBy;
  const sortDirection = orderBy.startsWith("-") ? "desc" : "asc";
  const sortQuery = (orderByValue) =>
    new URLSearchParams({ ...paginationParams, order_by: orderByValue, page: "1" }).toString();
  const sortLink = (field) => {
    const next =
      sortColumn === field
        ? sortDirection === "desc"
          ? field
          : `-${field}`
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

  const columns = [
    { label: "Job ID", field: "jid", sortable: true },
    ...(isStaff ? [{ label: "Sample Count", field: "sample_count", sortable: true }] : []),
    { label: "Performance Data", field: "performance_sort_rank", sortable: true },
    { label: "user", field: "username", sortable: true },
    { label: "Account", field: "account", sortable: true },
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

  return (
    <>
      <h1 className="h2 mb-3">{qname}</h1>
      {pageSummary ? (
        <p className="text-muted small mb-2 job-list-page-summary">{pageSummary}</p>
      ) : null}
      <h2 className="h5 mb-1">#Jobs = {nj}</h2>
      {totalNodeHours != null && (
        <p className="mb-3 text-muted small">
          Total node hours (all matching jobs): {formatDecimalStandard(totalNodeHours)}
        </p>
      )}
      {isStaff &&
      (queueWaitMeanHours != null || queueWaitStddevHours != null) ? (
        <div className="mb-3 text-muted small">
          {queueWaitMeanHours != null ? (
            <p className="mb-1">
              Mean queue wait (all matching jobs):{" "}
              {formatDecimalStandard(queueWaitMeanHours)} hours
            </p>
          ) : null}
          {queueWaitStddevHours != null ? (
            <p className="mb-0">
              Standard deviation: {formatDecimalStandard(queueWaitStddevHours)} hours
            </p>
          ) : null}
        </div>
      ) : null}

      <section
        id="job-list-distributions"
        role={isLgUp ? undefined : "tabpanel"}
        aria-labelledby={isLgUp ? undefined : tabChartsId}
        className="job-list-distributions mb-4"
        aria-label="Distributions for this list"
        hidden={!isLgUp && listViewTab !== "charts"}
      >
        <h2 className="h5 mb-2">Distributions for this list</h2>
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
          <ul className="nav nav-tabs" role="tablist" aria-label="Jobs list and charts">
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
                aria-controls="job-list-tabpanel-charts"
                tabIndex={listViewTab === "charts" ? 0 : -1}
                onClick={() => setListViewTab("charts")}
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
      {num_pages > 1 && (
        <nav className="pagination-wrapper" aria-label="Job list pagination">
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
      )}

      <div className="table-responsive job-list-table-wrapper" id="job-list-table">
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
          {job_list.map((job) => (
            <tr key={job.jid} style={{ backgroundColor: job.color || "#fff" }}>
              <td>
                <Link to={`/job/${job.jid}/`}>{job.jid}</Link>
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
                  <Link to={`/username/${job.username}/`}>{job.username}</Link>
                ) : (
                  "unknown"
                )}
              </td>
              <td>
                {job.account ? (
                  <Link to={`/account/${job.account}/`}>{job.account}</Link>
                ) : (
                  "None"
                )}
              </td>
              <td>{formatDateTime(job.start_time)}</td>
              <td>{formatDateTime(job.end_time)}</td>
              <td>{formatDecimalStandard(job.runtime)}</td>
              <td>
                {job.queue ? (
                  <Link to={`/queue/${encodeURIComponent(job.queue)}/`}>{job.queue}</Link>
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

      <p className="small mt-2 mb-0">
        <a href="#job-list-distributions" onClick={handleJumpToDistributions}>
          Jump to histograms for this list
        </a>
      </p>
      </div>
    </>
  );
}
