import { useEffect, useState } from "react";
import { useSearchParams, useParams, useLocation, Link } from "react-router-dom";
import { api } from "../api";
import BokehEmbed from "../components/BokehEmbed";
import HistogramThumbnails from "../components/HistogramThumbnails";
import LoadingMessage from "../components/LoadingMessage";

export default function JobList() {
  const [searchParams] = useSearchParams();
  const paramsFromRoute = useParams();
  const location = useLocation();
  const [data, setData] = useState(null);
  const [histograms, setHistograms] = useState(null);
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
    Promise.all([
      api.getJobList(params),
      api.getJobListHistograms(params),
    ])
      .then(([listData, histData]) => {
        setData(listData);
        setHistograms(histData);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
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
  const totalCpuHours = aggregates.total_cpu_hours;
  const script = histograms?.script ?? "";
  const div = histograms?.div ?? "";
  const plot_item = histograms?.plot_item ?? null;
  const histogramsList = histograms?.histograms ?? [];
  const { page, num_pages, has_previous, has_next, previous_page_number, next_page_number } =
    pagination;

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
        {histogramsList.length > 0 ? (
          <HistogramThumbnails histograms={histogramsList} />
        ) : (
          <BokehEmbed
            item={plot_item}
            script={script}
            div={div}
            id="index-bokeh"
            plotName="Job list histograms"
          />
        )}
      </center>
      <hr />
      <h4>#Jobs = {nj}</h4>
      {totalCpuHours != null && (
        <p className="mb-0">Total CPU hours (all matching jobs): {Number(totalCpuHours).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
      )}

      {num_pages > 1 && (
        <ul className="pagination">
          {page > 1 ? (
            <li>
              <Link to={`${location.pathname}?${paginationQuery(1)}`}>First</Link>
            </li>
          ) : (
            <li className="disabled">
              <span>First</span>
            </li>
          )}
          {has_previous ? (
            <li>
              <Link to={`${location.pathname}?${paginationQuery(previous_page_number)}`}>
                &laquo;
              </Link>
            </li>
          ) : (
            <li className="disabled">
              <span>&laquo;</span>
            </li>
          )}
          {Array.from({ length: num_pages }, (_, i) => i + 1).map((i) => (
            <li key={i} className={i === page ? "active" : ""}>
              {i === page ? (
                <span>{i}</span>
              ) : (
                <Link to={`${location.pathname}?${paginationQuery(i)}`}>{i}</Link>
              )}
            </li>
          ))}
          {has_next ? (
            <li>
              <Link to={`${location.pathname}?${paginationQuery(next_page_number)}`}>
                &raquo;
              </Link>
            </li>
          ) : (
            <li className="disabled">
              <span>&raquo;</span>
            </li>
          )}
          {page < num_pages ? (
            <li>
              <Link to={`${location.pathname}?${paginationQuery(num_pages)}`}>Last</Link>
            </li>
          ) : (
            <li className="disabled">
              <span>Last</span>
            </li>
          )}
        </ul>
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
              <td>{job.start_time}</td>
              <td>{job.end_time}</td>
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
