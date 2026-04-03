import { useEffect, useState } from "react";
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
import { useDocumentTitle } from "../utils/useDocumentTitle";

const ResolvedReactPaginate = ReactPaginate?.default || ReactPaginate;

export default function JobList() {
  const [searchParams] = useSearchParams();
  const paramsFromRoute = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [histograms, setHistograms] = useState(null);
  const [queueHistStatus, setQueueHistStatus] = useState({
    loading: false,
    error: null,
  });
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
    setQueueHistStatus({ loading: true, error: null });
    setMetricHistStatus(createInitialMetricStatus());

    const loadHistograms = async () => {
      let baseHistograms = [];
      try {
        const queueData = await api.getJobQueueHistograms(params);
        const queuePlots = queueData?.plots || [];
        baseHistograms = queuePlots
          .map((p) => normalizeJobListHistogramEntry(p))
          .filter(Boolean);
        setQueueHistStatus({ loading: false, error: null });
      } catch (e) {
        // Queue histogram errors should not break the main page; log to console for debugging.
        // eslint-disable-next-line no-console
        console.warn("Failed to load queue job list histograms", e);
        setQueueHistStatus({
          loading: false,
          error:
            e?.message ||
            "Failed to load queue histograms for this job list.",
        });
      }

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

      setHistograms([...baseHistograms, ...metricHistograms]);
    };

    loadHistograms();
  }, [searchParams, paramsFromRoute]);

  const documentTitleSegment =
    loading || error || !data ? "Job list" : data.qname || "Job list";
  useDocumentTitle(documentTitleSegment);

  if (loading) return <LoadingMessage message="Loading job list…" />;
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
  const { page, num_pages } = pagination;

  const paginationParams = buildJobListApiParams(searchParams, paramsFromRoute);

  const paginationQuery = (pageNum) =>
    new URLSearchParams({ ...paginationParams, page: String(pageNum) }).toString();

  // Sort: all columns except name. order_by from URL/response: e.g. "-end_time" (desc) or "username" (asc).
  const orderBy = searchParams.get("order_by") || responseOrderBy;
  const sortQuery = (orderByValue) =>
    new URLSearchParams({ ...paginationParams, order_by: orderByValue, page: "1" }).toString();
  const sortLink = (field) => {
    const isAsc = orderBy === field;
    const isDesc = orderBy === `-${field}`;
    const next = isDesc ? field : `-${field}`;
    return `${location.pathname}?${sortQuery(next)}`;
  };
  const sortIndicator = (field) => {
    if (orderBy === field) return " \u2191";
    if (orderBy === `-${field}`) return " \u2193";
    return "";
  };

  const ariaSortForField = (field, sortable) => {
    if (!sortable) return undefined;
    if (orderBy === field) return "ascending";
    if (orderBy === `-${field}`) return "descending";
    return "none";
  };

  const columns = [
    { label: "Job ID", field: "jid", sortable: true },
    { label: "Performance Data", field: "has_metrics", sortable: true },
    { label: "user", field: "username", sortable: true },
    { label: "Account", field: "account", sortable: true },
    { label: "start time", field: "start_time", sortable: true },
    { label: "end time", field: "end_time", sortable: true },
    { label: "run time (s)", field: "runtime", sortable: true },
    { label: "queue", field: "queue", sortable: true },
    { label: "name", field: "jobname", sortable: false },
    { label: "status", field: "state", sortable: true },
    { label: "cores", field: "ncores", sortable: true },
    { label: "nodes", field: "nhosts", sortable: true },
    { label: "node hrs", field: "node_hrs", sortable: true },
  ];

  return (
    <>
      <h1 className="h4">{qname}</h1>
      <div className="text-center">
        {queueHistStatus.loading && (
          <LoadingMessage message="Loading queue histograms…" />
        )}
        {!queueHistStatus.loading && queueHistStatus.error && (
          <p className="text-danger small mt-2" role="status">
            Queue histograms failed to load. Job list data is still available.
          </p>
        )}
        {metricNames.map((metric) => {
          const status = metricHistStatus[metric] || {
            loading: false,
            error: null,
          };
          const labelMap = {
            runtime: "Runtime",
            nhosts: "Node count",
            queue_wait: "Queue wait",
          };
          const friendlyName = labelMap[metric] || metric;
          return (
            <div key={metric}>
              {status.loading && (
                <LoadingMessage
                  message={`Loading ${friendlyName.toLowerCase()} histogram…`}
                />
              )}
              {!status.loading && status.error && (
                <p className="text-danger small mt-2" role="status">
                  {friendlyName} histogram failed to load. Job list data is
                  still available.
                </p>
              )}
            </div>
          );
        })}
        <HistogramThumbnails histograms={histograms} />
      </div>
      <hr />
      <h2 className="h5">#Jobs = {nj}</h2>
      {totalNodeHours != null && (
        <p className="mb-0">
          Total Node Hours (all matching jobs): {formatDecimalStandard(totalNodeHours)}
        </p>
      )}

      {num_pages > 1 && (
        <div className="pagination-wrapper">
          {page > 1 ? (
            <Link
              to={`${location.pathname}?${paginationQuery(1)}`}
              className="pagination-first"
            >
              First
            </Link>
          ) : (
            <span className="pagination-first disabled">First</span>
          )}
          <ResolvedReactPaginate
            forcePage={page - 1}
            pageCount={num_pages}
            onPageChange={({ selected }) =>
              navigate(`${location.pathname}?${paginationQuery(selected + 1)}`)
            }
            previousLabel="«"
            nextLabel="»"
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
          />
          {page < num_pages ? (
            <Link
              to={`${location.pathname}?${paginationQuery(num_pages)}`}
              className="pagination-last"
            >
              Last
            </Link>
          ) : (
            <span className="pagination-last disabled">Last</span>
          )}
        </div>
      )}

      <div className="table-responsive">
        <table className="table table-sm table-bordered">
          <caption className="visually-hidden">
            Job list for {qname}. {nj} jobs.
          </caption>
          <thead>
            <tr>
              {columns.map(({ label, field, sortable }) => (
              <th key={field} aria-sort={ariaSortForField(field, sortable)}>
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
              <td>
                {job.has_metrics ? (
                  <span className="badge text-bg-success" aria-label="Performance data available">
                    True
                  </span>
                ) : (
                  <span className="badge text-bg-secondary" aria-label="No performance data">
                    False
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
              <td>{job.jobname}</td>
              <td>{job.state}</td>
              <td>{formatDecimalStandard(job.ncores)}</td>
              <td>{formatDecimalStandard(job.nhosts)}</td>
              <td>{formatDecimalStandard(job.node_hrs)}</td>
            </tr>
          ))}
        </tbody>
        </table>
      </div>
    </>
  );
}
