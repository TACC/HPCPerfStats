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

function PlotPanel({
  item,
  id,
  plotName,
  unavailableReason,
  onZoom,
}) {
  const [isPlotRendered, setIsPlotRendered] = useState(false);

  return (
    <div>
      <BokehEmbed
        item={item}
        id={id}
        plotName={plotName}
        unavailableReason={unavailableReason}
        onPlotReadyChange={setIsPlotRendered}
      />
      {isPlotRendered ? (
        <div style={{ marginTop: "0.35rem", textAlign: "center" }}>
          <button
            type="button"
            className="btn btn-link p-0"
            onClick={onZoom}
            aria-label={`Zoom ${plotName}`}
          >
            zoom
          </button>
        </div>
      ) : null}
    </div>
  );
}

const JOB_PLOT_CONFIGS = [
  {
    key: "summary_plot",
    panelKey: "summary",
    idPrefix: "job-mscript",
    plotName: "Summary plot",
  },
  {
    key: "heatmap",
    panelKey: "heatmap",
    idPrefix: "job-hscript",
    plotName: "Heatmap",
  },
  {
    key: "roofline",
    panelKey: "cpu-roofline",
    idPrefix: "job-roofline",
    plotName: "CPU Roofline",
  },
  {
    key: "gpu_roofline",
    panelKey: "gpu-roofline",
    idPrefix: "job-gpu-roofline",
    plotName: "GPU Roofline",
  },
];

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
  const [zoomPlotKey, setZoomPlotKey] = useState(null);
  const [zoomPlotState, setZoomPlotState] = useState({
    loading: false,
    item: null,
    unavailableReason: null,
  });

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

        const emptyPlotsState = JOB_PLOT_CONFIGS.reduce((acc, config) => {
          acc[config.key] = {
            loading: true,
            plotItem: null,
            unavailableReason: null,
          };
          return acc;
        }, {});
        setPlots(emptyPlotsState);
        setPlotsLoading(true);

        const fetchPlotWithPolling = async (plotKey) => {
          let keepLoading = false;
          try {
            const plotResponse = await api.getJobPlots(pk, plotKey);
            if (cancelled) return;

            if (plotResponse?.status === "loading") {
              keepLoading = true;
              const retryAfterMs = Math.max(
                250,
                Number(plotResponse.retry_after_seconds ?? 2) * 1000
              );
              setTimeout(() => {
                if (!cancelled) fetchPlotWithPolling(plotKey);
              }, retryAfterMs);
              return;
            }

            setPlots((prev) => ({
              ...(prev || {}),
              [plotKey]: {
                loading: false,
                plotItem: plotResponse?.plot_item ?? null,
                unavailableReason: plotResponse?.unavailable_reason ?? null,
              },
            }));
          } catch {
            if (cancelled) return;
            // eslint-disable-next-line no-console
            console.warn(`Failed to load ${plotKey} for job ${pk}`);
            setPlots((prev) => ({
              ...(prev || {}),
              [plotKey]: {
                loading: false,
                plotItem: null,
                unavailableReason: null,
              },
            }));
          } finally {
            if (cancelled || keepLoading) return;
          }
        };

        JOB_PLOT_CONFIGS.forEach((config) => {
          fetchPlotWithPolling(config.key);
        });

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

  useEffect(() => {
    if (!plots) return;
    const allFinished = JOB_PLOT_CONFIGS.every(
      (config) => plots?.[config.key] && plots[config.key].loading === false
    );
    if (allFinished) setPlotsLoading(false);
  }, [plots]);

  useEffect(() => {
    if (!zoomPlotKey || !pk) {
      setZoomPlotState({ loading: false, item: null, unavailableReason: null });
      return;
    }

    const selectedConfig = JOB_PLOT_CONFIGS.find((config) => config.panelKey === zoomPlotKey);
    if (!selectedConfig) return;

    let cancelled = false;
    setZoomPlotState({ loading: true, item: null, unavailableReason: null });

    const fetchZoomPlot = async () => {
      try {
        const zoomResponse = await api.getJobPlots(pk, selectedConfig.key, true);
        if (cancelled) return;
        if (zoomResponse?.status === "loading") {
          const retryAfterMs = Math.max(
            250,
            Number(zoomResponse.retry_after_seconds ?? 2) * 1000
          );
          setTimeout(() => {
            if (!cancelled) fetchZoomPlot();
          }, retryAfterMs);
          return;
        }
        setZoomPlotState({
          loading: false,
          item: zoomResponse?.plot_item ?? null,
          unavailableReason: zoomResponse?.unavailable_reason ?? null,
        });
      } catch {
        if (cancelled) return;
        setZoomPlotState({ loading: false, item: null, unavailableReason: null });
      }
    };

    fetchZoomPlot();
    return () => {
      cancelled = true;
    };
  }, [zoomPlotKey, pk]);

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

  const hasDeviceData = Object.keys(schema).length > 0;
  const plotPanels = JOB_PLOT_CONFIGS.map((config) => ({
    key: config.panelKey,
    item: plots?.[config.key]?.plotItem ?? null,
    id: `${config.idPrefix}-${pk}`,
    plotName: config.plotName,
    unavailableReason: plots?.[config.key]?.unavailableReason ?? null,
  }));
  const zoomedPanel = plotPanels.find((panel) => panel.key === zoomPlotKey) || null;

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
                    <b>Total GPUs allocated:</b>
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
            {[plotPanels.slice(0, 2), plotPanels.slice(2, 4)].map((row, rowIndex) => (
              <tr key={`plot-row-${rowIndex}`}>
                {row.map((panel) => (
                  <td key={panel.key}>
                    <PlotPanel
                      item={panel.item}
                      id={panel.id}
                      plotName={panel.plotName}
                      unavailableReason={panel.unavailableReason}
                      onZoom={() => setZoomPlotKey(panel.key)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </center>

      {zoomedPanel ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`${zoomedPanel.plotName} zoom view`}
          style={{
            position: "fixed",
            inset: 0,
            width: "100vw",
            height: "100vh",
            zIndex: 1100,
            backgroundColor: "rgba(0, 0, 0, 0.72)",
            display: "flex",
            alignItems: "stretch",
            justifyContent: "stretch",
            padding: "1rem",
            boxSizing: "border-box",
          }}
        >
          <div
            style={{
              position: "relative",
              width: "100%",
              height: "100%",
              backgroundColor: "#fff",
              borderRadius: 6,
              padding: "2.25rem 1rem 1rem 1rem",
              overflow: "auto",
            }}
          >
            <button
              type="button"
              className="btn btn-link p-0"
              aria-label="Close zoom window"
              onClick={() => setZoomPlotKey(null)}
              style={{ position: "absolute", top: "0.5rem", right: "0.75rem" }}
            >
              x
            </button>
            <div style={{ width: "100%", height: "100%" }}>
              {zoomPlotState.loading && (
                <LoadingMessage message={`Loading ${zoomedPanel.plotName}…`} />
              )}
              <BokehEmbed
                item={zoomPlotState.item}
                id={`${zoomedPanel.id}-zoom`}
                plotName={zoomedPanel.plotName}
                unavailableReason={zoomPlotState.unavailableReason || zoomedPanel.unavailableReason}
                fillHeight
                maximizeInContainer
              />
            </div>
          </div>
        </div>
      ) : null}

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
