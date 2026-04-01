import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import BokehEmbed from "../components/BokehEmbed";
import LoadingMessage from "../components/LoadingMessage";
import { formatDateTime } from "../utils/formatDateTime";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { useSession } from "../session-context";

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
        <strong>{title}{empty ? " (Data not available.)" : ""}</strong>
      </button>
      {open && <div className="border border-top-0 rounded-bottom p-2">{children}</div>}
    </div>
  );
}

function formatJobMetricCell(obj, isStaff) {
  if (obj.value != null && obj.value !== "") {
    return formatDecimalStandard(obj.value);
  }
  if (isStaff) {
    return obj.no_data_reason || "Data not available.";
  }
  return "Data not available.";
}

export default function JobDetail() {
  const session = useSession();
  const isStaff = !!session?.is_staff;
  const { pk } = useParams();
  const [data, setData] = useState(null);
  const [plots, setPlots] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [plotsLoading, setPlotsLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);

  useEffect(() => {
    if (!pk) return;

    let cancelled = false;

    setError(null);
    setData(null);
    setPlots(null);
    setLoading(true);
    setPlotsLoading(true);
    setDetailsLoading(false);

    // 1) Render quickly with a lightweight job_detail response.
    api
      .getJobDetailLight(pk)
      .then((jobLightData) => {
        if (cancelled) return;
        setData(jobLightData);
        setLoading(false);

        // 2) Fetch full job detail in the background to fill heavy fields.
        setDetailsLoading(true);

        // 2) Load plots after we've rendered the job page.
        const fetchPlotsWithPolling = async () => {
          let keepLoading = false;
          try {
            const jobPlots = await api.getJobPlots(pk);
            if (cancelled) return;

            if (jobPlots?.status === "loading") {
              keepLoading = true;
              const retryAfterMs = Math.max(
                250,
                Number(jobPlots.retry_after_seconds ?? 2) * 1000
              );
              setTimeout(() => {
                if (!cancelled) fetchPlotsWithPolling();
              }, retryAfterMs);
              return;
            }

            setPlots(jobPlots);
          } catch {
            // eslint-disable-next-line no-console
            console.warn("Failed to load job plots");
          } finally {
            if (cancelled) return;
            if (!keepLoading) {
              setPlotsLoading(false);
            }
          }
        };
        fetchPlotsWithPolling();

        api
          .getJobDetail(pk)
          .then((jobFullData) => {
            if (cancelled) return;
            setData(jobFullData);
          })
          .catch(() => {
            // If the full detail fetch fails, keep the lightweight data.
          })
          .finally(() => {
            if (cancelled) return;
            setDetailsLoading(false);
          });
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e.message);
        setPlotsLoading(false);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [pk]);

  if (loading) return <LoadingMessage message="Loading job detail…" />;
  if (error) return <div className="container text-danger">Error: {error}</div>;
  if (!data) return null;

  const job = data.job_data || {};
  const {
    host_list = [],
    fsio = {},
    xalt_data = {},
    schema = {},
    client_url,
    server_url,
    gpu_active,
    gpu_utilization_max,
    gpu_utilization_mean,
    gpu_count,
    metrics_list = [],
    proc_list = [],
  } = data;

  const {
    mplot_item,
    mplot_unavailable_reason,
    hplot_item,
    hplot_unavailable_reason,
    rplot_item,
    rplot_unavailable_reason,
    grplot_item,
    grplot_unavailable_reason,
  } = plots || {};

  const hasDeviceData = Object.keys(schema).length > 0;

  return (
    <>
      <div>
        <h2>Job Detail</h2>
        <div className="table-responsive">
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
              <td>{formatDecimalStandard(job.runtime)}</td>
              <td>{formatDecimalStandard(job.timelimit)}</td>
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
            </tr>
          </tbody>
          </table>
        </div>
      </div>

      <div className="row">
        <div className="col-md-3">
          <div className="table-responsive">
            <table className="table table-sm table-bordered">
            <thead>
              <tr>
                <th>File System</th>
                <th>MB Read</th>
                <th>MB Written</th>
              </tr>
            </thead>
            <tbody>
              {detailsLoading ? (
                <tr>
                  <td colSpan={3} className="text-muted">
                    Loading file system data…
                  </td>
                </tr>
              ) : Object.keys(fsio).length === 0 ? (
                <tr>
                  <td colSpan={3} className="text-muted">
                    Data not available.
                  </td>
                </tr>
              ) : (
                Object.entries(fsio).map(([key, val]) => (
                  <tr key={key}>
                    <td>{key}</td>
                    <td>{formatDecimalStandard(val[0])}</td>
                    <td>{formatDecimalStandard(val[1])}</td>
                  </tr>
                ))
              )}
            </tbody>
            </table>
          </div>
        </div>
      </div>

      <div
        className="col-sm-12 col-md-auto"
        style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}
      >
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

      {(detailsLoading || gpu_active != null || gpu_count != null) && (
        <>
          {detailsLoading && gpu_active == null && gpu_count == null ? (
            <p className="text-muted" role="status" style={{ marginTop: "1rem" }}>
              Loading GPU statistics…
            </p>
          ) : (
            <table border="1" style={{ marginTop: "1rem" }}>
              <tbody>
                <tr>
                  <td style={{ border: "1px solid lightgrey" }}>
                    <b>Total GPUs per Machine:</b>
                  </td>
                  <td style={{ border: "1px solid lightgrey", textAlign: "right" }}>
                    {formatDecimalStandard(gpu_count)}
                  </td>
                </tr>
                <tr>
                  <td style={{ border: "1px solid lightgrey" }}>
                    <b>Number of GPUs active:</b>
                  </td>
                  <td style={{ border: "1px solid lightgrey", textAlign: "right" }}>
                    {formatDecimalStandard(gpu_active)}
                  </td>
                </tr>
                <tr>
                  <td style={{ border: "1px solid lightgrey" }}>
                    <b>Max GPU Utilization:</b>
                  </td>
                  <td style={{ border: "1px solid lightgrey", textAlign: "right" }}>
                    {gpu_utilization_max != null && gpu_utilization_max !== ""
                      ? `${formatDecimalStandard(gpu_utilization_max)}%`
                      : ""}
                  </td>
                </tr>
                <tr>
                  <td style={{ border: "1px solid lightgrey" }}>
                    <b>Mean GPU Utilization:</b>
                  </td>
                  <td style={{ border: "1px solid lightgrey", textAlign: "right" }}>
                    {gpu_utilization_mean != null && gpu_utilization_mean !== ""
                      ? `${formatDecimalStandard(gpu_utilization_mean)}%`
                      : ""}
                  </td>
                </tr>
              </tbody>
            </table>
          )}
        </>
      )}

      <div className="row" style={{ marginTop: "1rem" }}>
        <CollapsibleSection
          title="Processes"
          empty={!detailsLoading && !(proc_list || []).length}
        >
          {detailsLoading ? (
            <div className="text-muted">Loading processes…</div>
          ) : (
            <table className="table table-sm table-bordered">
              <tbody>
                {(proc_list || []).map((proc, i) => (
                  <tr key={i}>
                    <td>{proc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CollapsibleSection>
        <CollapsibleSection
          title="Job-level Metrics"
          empty={!detailsLoading && !metrics_list.length}
        >
          {detailsLoading ? (
            <div className="text-muted">Loading job-level metrics…</div>
          ) : (
            <table className="table table-sm table-bordered">
              <tbody>
                {(metrics_list || []).map((obj) => (
                  <tr key={obj.metric}>
                    <th>
                      {obj.metric} [{obj.units}]
                    </th>
                    <td className={obj.value != null && obj.value !== "" ? "" : "text-muted"}>
                      {formatJobMetricCell(obj, isStaff)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CollapsibleSection>
        <CollapsibleSection
          title="Execution Parameters"
          empty={
            !detailsLoading &&
            !(xalt_data.exec_path || []).length &&
            !(xalt_data.cwd || []).length &&
            !(xalt_data.libset || []).length
          }
        >
          {detailsLoading ? (
            <div className="text-muted">Loading execution parameters…</div>
          ) : (
            <>
              <table className="table table-sm table-bordered">
                <tbody>
                  <tr>
                    <td>Executable Path</td>
                    <td>
                      {(xalt_data.exec_path || []).length === 0 ? (
                        <span className="text-muted">Data not available.</span>
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
                        <span className="text-muted">Data not available.</span>
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
                        Data not available.
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
            </>
          )}
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
      <center className="job-detail-plots">
        <h3>Host-level Plots</h3>
        {plotsLoading && (
          <LoadingMessage message="Loading job plots…" />
        )}
        <table>
          <tbody>
            <tr>
              <td>
                <BokehEmbed
                  item={mplot_item}
                  id={`job-mscript-${pk}`}
                  plotName="Summary plot"
                  unavailableReason={mplot_unavailable_reason}
                />
              </td>
              <td>
                <BokehEmbed
                  item={hplot_item}
                  id={`job-hscript-${pk}`}
                  plotName="Heatmap"
                  unavailableReason={hplot_unavailable_reason}
                />
              </td>
            </tr>
            <tr>
              <td>
                <BokehEmbed
                  item={rplot_item}
                  id={`job-roofline-${pk}`}
                  plotName="CPU Roofline"
                  unavailableReason={rplot_unavailable_reason}
                />
              </td>
              <td>
                <BokehEmbed
                  item={grplot_item}
                  id={`job-gpu-roofline-${pk}`}
                  plotName="GPU Roofline"
                  unavailableReason={grplot_unavailable_reason}
                />
              </td>
            </tr>
          </tbody>
        </table>
      </center>

      <center>
        <h4>Device Data and Plots</h4>
        {detailsLoading ? (
          <p className="text-muted" role="status">
            Loading device data and plots…
          </p>
        ) : !hasDeviceData ? (
          <p className="text-muted" role="status">
            Data not available.
          </p>
        ) : (
          <div className="table-responsive">
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
                      {Array.isArray(event) ? event.join(", ") : event}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </center>
    </>
  );
}
