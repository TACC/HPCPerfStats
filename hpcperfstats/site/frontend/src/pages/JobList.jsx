import { useEffect, useState } from "react";
import { useSearchParams, useParams, useLocation, Link, useNavigate } from "react-router-dom";
import ReactPaginate from "react-paginate";
import { api } from "../api";
import HistogramThumbnails from "../components/HistogramThumbnails";
import LoadingMessage from "../components/LoadingMessage";
import { formatDateTime } from "../utils/formatDateTime";

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
    const params = { ...Object.fromEntries(searchParams.entries()) };
    if (paramsFromRoute.year) params.end_time__date = paramsFromRoute.year;
    if (paramsFromRoute.date) params.end_time__date = paramsFromRoute.date;
    if (paramsFromRoute.username) params.username = paramsFromRoute.username;
    if (paramsFromRoute.account) params.account = paramsFromRoute.account;
    if (paramsFromRoute.queue) params.queue = paramsFromRoute.queue;
    if (paramsFromRoute.host) params.host = paramsFromRoute.host;
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
        baseHistograms = queuePlots.map((p) => ({
          title: p.title,
          plot_item_thumb: p.plot_item_thumb,
          plot_item_full: p.plot_item_full,
        }));
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
            if (
              !metricData ||
              !metricData.plot_item_thumb ||
              !metricData.plot_item_full
            ) {
              return null;
            }
            setMetricHistStatus((prev) => ({
              ...prev,
              [metric]: { loading: false, error: null },
            }));
            return {
              title: metricData.title || metricData.metric || metric,
              plot_item_thumb: metricData.plot_item_thumb,
              plot_item_full: metricData.plot_item_full,
            };
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

  if (loading) return <LoadingMessage message="Loading job list…" />;
  if (error) return <div className="container text-danger">Error: {error}</div>;
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
  const histogramsList = histograms || [];
  const { page, num_pages } = pagination;

  const paginationParams = Object.fromEntries(searchParams.entries());
  if (paramsFromRoute.year) paginationParams.end_time__date = paramsFromRoute.year;
  if (paramsFromRoute.date) paginationParams.end_time__date = paramsFromRoute.date;
  if (paramsFromRoute.username) paginationParams.username = paramsFromRoute.username;
  if (paramsFromRoute.account) paginationParams.account = paramsFromRoute.account;
  if (paramsFromRoute.queue) paginationParams.queue = paramsFromRoute.queue;
  if (paramsFromRoute.host) paginationParams.host = paramsFromRoute.host;

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

  const columns = [
    { label: "Job ID", field: "jid", sortable: true },
    { label: "Data", field: "has_metrics", sortable: true },
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
      <h4>{qname}</h4>
      <center>
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
        <HistogramThumbnails histograms={histogramsList} />
      </center>
      <hr />
      <h4>#Jobs = {nj}</h4>
      {totalNodeHours != null && (
        <p className="mb-0">Total Node Hours (all matching jobs): {Number(totalNodeHours).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
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
          <ReactPaginate
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

      <table className="table table-sm table-bordered">
        <thead>
          <tr>
            {columns.map(({ label, field, sortable }) => (
              <th key={field}>
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
              <td>{job.has_metrics ? "True" : "False"}</td>
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
              <td>{job.runtime}</td>
              <td>
                {job.queue ? (
                  <Link to={`/queue/${encodeURIComponent(job.queue)}/`}>{job.queue}</Link>
                ) : (
                  ""
                )}
              </td>
              <td>{job.jobname}</td>
              <td>{job.state}</td>
              <td>{job.ncores}</td>
              <td>{job.nhosts}</td>
              <td>{job.node_hrs != null ? Number(job.node_hrs).toFixed(2) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
