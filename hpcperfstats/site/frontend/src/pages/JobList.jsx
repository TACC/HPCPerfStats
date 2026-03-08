import { useEffect, useState } from "react";
import { useSearchParams, useParams, useLocation, Link } from "react-router-dom";
import { api } from "../api";
import BokehEmbed from "../components/BokehEmbed";
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
    if (paramsFromRoute.date) params.end_time__date = paramsFromRoute.date;
    if (paramsFromRoute.username) params.username = paramsFromRoute.username;
    if (paramsFromRoute.account) params.account = paramsFromRoute.account;
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
    current_path,
    qname,
    pagination = {},
  } = data;
  const script = histograms?.script ?? "";
  const div = histograms?.div ?? "";
  const plot_item = histograms?.plot_item ?? null;
  const { page, num_pages, has_previous, has_next, previous_page_number, next_page_number } =
    pagination;

  const paginationParams = Object.fromEntries(searchParams.entries());
  if (paramsFromRoute.date) paginationParams.end_time__date = paramsFromRoute.date;
  if (paramsFromRoute.username) paginationParams.username = paramsFromRoute.username;
  if (paramsFromRoute.account) paginationParams.account = paramsFromRoute.account;
  if (paramsFromRoute.host) paginationParams.host = paramsFromRoute.host;

  const paginationQuery = (pageNum) =>
    new URLSearchParams({ ...paginationParams, page: String(pageNum) }).toString();

  return (
    <>
      <h4>{qname}</h4>
      <center>
        <BokehEmbed
          item={plot_item}
          script={script}
          div={div}
          id="index-bokeh"
          plotName="Job list histograms"
        />
      </center>
      <hr />
      <h4>#Jobs = {nj}</h4>

      {num_pages > 1 && (
        <ul className="pagination">
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
        </ul>
      )}

      <table className="table table-condensed table-bordered">
        <thead>
          <tr>
            <th>Job ID</th>
            <th>Data</th>
            <th>user</th>
            <th>Account</th>
            <th>start time</th>
            <th>end time</th>
            <th>run time (s)</th>
            <th>queue</th>
            <th>name</th>
            <th>status</th>
            <th>cores</th>
            <th>nodes</th>
            <th>node hrs</th>
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
              <td>{job.queue}</td>
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
