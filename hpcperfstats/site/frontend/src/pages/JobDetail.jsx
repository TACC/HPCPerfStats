import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import BokehEmbed from "../components/BokehEmbed";

export default function JobDetail() {
  const { pk } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!pk) return;
    api
      .getJobDetail(pk)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [pk]);

  if (loading) return <div className="container">Loading…</div>;
  if (error) return <div className="container text-danger">Error: {error}</div>;
  if (!data) return null;

  const job = data.job_data || {};
  const {
    host_list = [],
    fsio = {},
    xalt_data = {},
    mscript,
    mdiv,
    mplot_item,
    hscript,
    hdiv,
    schema = {},
    client_url,
    server_url,
    gpu_active,
    gpu_utilization_max,
    gpu_utilization_mean,
    metrics_list = [],
    proc_list = [],
  } = data;

  return (
    <>
      <div>
        <h2>Job Detail</h2>
        <table className="table table-condensed table-bordered">
          <thead>
            <tr>
              <th>Job ID</th>
              <th>user</th>
              <th>project</th>
              <th>start time</th>
              <th>end time</th>
              <th>run time (s)</th>
              <th>requested time (s)</th>
              <th>queue</th>
              <th>name</th>
              <th>status</th>
              <th>ncores</th>
              <th>nnodes</th>
            </tr>
          </thead>
          <tbody>
            <tr style={{ backgroundColor: job.color || "#fff" }}>
              <td>
                <Link to={`/job/${job.jid}`}>{job.jid}</Link>
              </td>
              <td>
                {job.username ? (
                  <Link to={`/username/${job.username}/`}>{job.username}</Link>
                ) : (
                  "Unknown"
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
              <td>{job.timelimit}</td>
              <td>{job.queue}</td>
              <td>{job.jobname}</td>
              <td>{job.state}</td>
              <td>{job.ncores}</td>
              <td>{job.nhosts}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="row">
        <div className="col-md-3">
          <table className="table table-condensed table-bordered">
            <thead>
              <tr>
                <th>File System</th>
                <th>MB Read</th>
                <th>MB Written</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(fsio).map(([key, val]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td>{Number(val[0]).toExponential(1)}</td>
                  <td>{Number(val[1]).toExponential(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="col-md-3">
          <table className="table table-condensed">
            <tbody>
              <tr>
                <td>Executable Path</td>
                <td>
                  {(xalt_data.exec_path || []).map((item, i) => (
                    <span key={i}>{item}<br /></span>
                  ))}
                </td>
              </tr>
              <tr>
                <td>Working Directory</td>
                <td>
                  {(xalt_data.cwd || []).map((item, i) => (
                    <span key={i}>{item}<br /></span>
                  ))}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="col-sm-20">
        {client_url && <a href={client_url}>Client Logs</a>}
        {server_url && <a href={server_url}> Server Logs</a>}
      </div>

      {gpu_active != null && (
        <table border="1" style={{ marginTop: "1rem" }}>
          <caption>GPU Statistics</caption>
          <tbody>
            <tr>
              <td style={{ border: "1px solid lightgrey" }}>
                <b>Number of GPUs active:</b>
              </td>
              <td style={{ border: "1px solid lightgrey", textAlign: "right" }}>
                {gpu_active}
              </td>
            </tr>
            <tr>
              <td style={{ border: "1px solid lightgrey" }}>
                <b>Max GPU Utilization:</b>
              </td>
              <td style={{ border: "1px solid lightgrey", textAlign: "right" }}>
                {gpu_utilization_max}%
              </td>
            </tr>
            <tr>
              <td style={{ border: "1px solid lightgrey" }}>
                <b>Mean GPU Utilization:</b>
              </td>
              <td style={{ border: "1px solid lightgrey", textAlign: "right" }}>
                {gpu_utilization_mean != null ? gpu_utilization_mean.toFixed(1) : ""}%
              </td>
            </tr>
          </tbody>
        </table>
      )}

      <div className="row" style={{ marginTop: "1rem" }}>
        <div className="col-md-3">
          <h4>Processes</h4>
          <table className="table table-condensed table-bordered">
            <tbody>
              {(proc_list || []).map((proc, i) => (
                <tr key={i}>
                  <td>{proc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="col-md-3">
          <h4>Job-level Metrics</h4>
          <table className="table table-condensed table-bordered">
            <tbody>
              {metrics_list.map((obj, i) => (
                <tr key={i}>
                  <th>{obj.metric} [{obj.units}]</th>
                  <td>{obj.value != null ? Number(obj.value).toFixed(2) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="col-md-3">
          <h4>Modules and Libraries</h4>
          <table className="table table-condensed table-bordered">
            <thead>
              <tr>
                <th>Module</th>
                <th>Library</th>
              </tr>
            </thead>
            <tbody>
              {(xalt_data.libset || []).map((item, i) => (
                <tr key={i}>
                  <td>{item[1] === "none" ? "system" : item[1]}</td>
                  <td>{item[0]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="col-md-3">
          <h4>Hosts</h4>
          <table className="table table-condensed table-bordered">
            <tbody>
              {host_list.map((host, i) => (
                <tr key={i}>
                  <td>{host}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <hr />
      <center>
        <h3>Host-level Plots</h3>
        <table>
          <tbody>
            <tr>
              <td>
                <BokehEmbed
                  item={mplot_item}
                  script={mscript}
                  div={mdiv}
                  id="job-mscript"
                />
              </td>
              <td>
                <BokehEmbed script={hscript} div={hdiv} id="job-hscript" />
              </td>
            </tr>
          </tbody>
        </table>
      </center>

      <center>
        <h4>Device Data and Plots</h4>
        <table className="table table-condensed table-bordered">
          <thead>
            <tr>
              <th>Type Name</th>
              <th>Recorded Performance Events</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(schema).map(([type_name, event]) => (
              <tr key={type_name}>
                <td>
                  <Link to={`/job/${job.jid}/${type_name}/`}>{type_name}</Link>
                </td>
                <td style={{ textAlign: "left" }}>{event}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </center>
    </>
  );
}
