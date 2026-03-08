import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import BokehEmbed from "../components/BokehEmbed";
import LoadingMessage from "../components/LoadingMessage";

/** Format ISO date string for display; returns empty string if invalid or missing. */
function formatDateTime(isoString) {
  if (isoString == null || isoString === "") return "";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return String(isoString);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

function CollapsibleSection({ title, children, defaultOpen = false, empty = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="col-md-3 mb-2">
      <button
        type="button"
        className="btn btn-outline-secondary btn-sm d-flex align-items-center gap-2 w-100 text-start"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="flex-shrink-0" style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 0.2s", display: "inline-block" }}>
          ▶
        </span>
        <strong>{title}{empty ? " (data unavailable)" : ""}</strong>
      </button>
      {open && <div className="border border-top-0 rounded-bottom p-2">{children}</div>}
    </div>
  );
}

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

  if (loading) return <LoadingMessage message="Loading job detail…" />;
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
    mplot_unavailable_reason,
    hscript,
    hdiv,
    hplot_item,
    hplot_unavailable_reason,
    rscript,
    rdiv,
    rplot_item,
    rplot_unavailable_reason,
    schema = {},
    client_url,
    server_url,
    gpu_active,
    gpu_utilization_max,
    gpu_utilization_mean,
    metrics_list = [],
    proc_list = [],
  } = data;

  const hasDeviceData = Object.keys(schema).length > 0;

  return (
    <>
      <div>
        <h2>Job Detail</h2>
        <table className="table table-sm table-bordered">
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
              <td>{formatDateTime(job.start_time)}</td>
              <td>{formatDateTime(job.end_time)}</td>
              <td>{job.runtime}</td>
              <td>{job.timelimit}</td>
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
            </tr>
          </tbody>
        </table>
      </div>

      <div className="row">
        <div className="col-md-3">
          <table className="table table-sm table-bordered">
            <thead>
              <tr>
                <th>File System</th>
                <th>MB Read</th>
                <th>MB Written</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(fsio).length === 0 ? (
                <tr>
                  <td colSpan={3} className="text-muted">
                    No file system data available.
                  </td>
                </tr>
              ) : (
                Object.entries(fsio).map(([key, val]) => (
                  <tr key={key}>
                    <td>{key}</td>
                    <td>{Number(val[0]).toExponential(1)}</td>
                    <td>{Number(val[1]).toExponential(1)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="col-sm-20" style={{ display: "flex", gap: "0.5rem" }}>
        {client_url && (
          <a
            href={client_url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-outline-secondary btn-sm"
          >
            Client Logs
          </a>
        )}
        {server_url && (
          <a
            href={server_url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-outline-secondary btn-sm"
          >
            Server Logs
          </a>
        )}
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
        <CollapsibleSection title="Processes" empty={!(proc_list || []).length}>
          <table className="table table-sm table-bordered">
            <tbody>
              {(proc_list || []).map((proc, i) => (
                <tr key={i}>
                  <td>{proc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CollapsibleSection>
        <CollapsibleSection title="Job-level Metrics" empty={!metrics_list.length}>
          <table className="table table-sm table-bordered">
            <tbody>
              {metrics_list.map((obj, i) => (
                <tr key={i}>
                  <th>{obj.metric} [{obj.units}]</th>
                  <td>{obj.value != null ? Number(obj.value).toFixed(2) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CollapsibleSection>
        <CollapsibleSection
          title="Execution Parameters"
          empty={
            !(xalt_data.exec_path || []).length &&
            !(xalt_data.cwd || []).length &&
            !(xalt_data.libset || []).length
          }
        >
          <table className="table table-sm table-bordered">
            <tbody>
              <tr>
                <td>Executable Path</td>
                <td>
                  {(xalt_data.exec_path || []).length === 0 ? (
                    <span className="text-muted">No XALT data available.</span>
                  ) : (
                    (xalt_data.exec_path || []).map((item, i) => (
                      <span key={`exec-${i}`}>{item}<br /></span>
                    ))
                  )}
                </td>
              </tr>
              <tr>
                <td>Working Directory</td>
                <td>
                  {(xalt_data.cwd || []).length === 0 ? (
                    <span className="text-muted">No XALT data available.</span>
                  ) : (
                    (xalt_data.cwd || []).map((item, i) => (
                      <span key={`cwd-${i}`}>{item}<br /></span>
                    ))
                  )}
                </td>
              </tr>
            </tbody>
          </table>
          <table className="table table-sm table-bordered">
            <thead>
              <tr>
                <th>Module</th>
                <th>Library</th>
              </tr>
            </thead>
            <tbody>
              {(xalt_data.libset || []).length === 0 ? (
                <tr>
                  <td colSpan={2} className="text-muted">
                    No XALT data available.
                  </td>
                </tr>
              ) : (
                (xalt_data.libset || []).map((item, i) => (
                  <tr key={i}>
                    <td>{item[1] === "none" ? "system" : item[1]}</td>
                    <td>{item[0]}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </CollapsibleSection>
        <CollapsibleSection title="Hosts" empty={!host_list.length}>
          <table className="table table-sm table-bordered">
            <tbody>
              {host_list.map((host, i) => (
                <tr key={i}>
                  <td>{host}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CollapsibleSection>
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
                  plotName="Summary plot"
                  unavailableReason={mplot_unavailable_reason}
                />
              </td>
              <td>
                <BokehEmbed
                  item={hplot_item}
                  script={hscript}
                  div={hdiv}
                  id="job-hscript"
                  plotName="Heatmap"
                  unavailableReason={hplot_unavailable_reason}
                />
              </td>
            </tr>
            <tr>
              <td colSpan={2}>
                <BokehEmbed
                  item={rplot_item}
                  script={rscript}
                  div={rdiv}
                  id="job-roofline"
                  plotName="Roofline"
                  unavailableReason={rplot_unavailable_reason}
                />
              </td>
            </tr>
          </tbody>
        </table>
      </center>

      <center>
        <h4>Device Data and Plots</h4>
        {!hasDeviceData ? (
          <p className="text-muted" role="status">
            No device data or plots available for this job.
          </p>
        ) : (
          <table className="table table-sm table-bordered">
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
                  <td style={{ textAlign: "left" }}>
                    {Array.isArray(event)
                      ? event.join(", ")
                      : event}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </center>
    </>
  );
}
