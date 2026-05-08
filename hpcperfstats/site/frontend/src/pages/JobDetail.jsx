import { useEffect, memo, useId, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehEmbed from "../components/BokehEmbed";
import LoadingMessage from "../components/LoadingMessage";
import { formatDateTime } from "../utils/formatDateTime";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { useSession } from "../session-context";
import { VariableInfoLabel } from "../components/VariableInfoLabel";
import { scheduleJobPlotsRetry } from "../utils/job-plots-polling";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { getJobMetricShortLabel } from "../utils/jobMetricDisplayLabels";

function formatJobMetricCell(obj, isStaff) {
  if (obj.value != null && obj.value !== "") {
    return formatDecimalStandard(obj.value);
  }
  if (isStaff) {
    return obj.no_data_reason || "Data not available.";
  }
  return "Data not available.";
}

function buildJobDetailTitle({ error, loading, data, pk }) {
  if (error) return pk ? `Job ${pk} (error)` : "Job detail";
  if (loading && pk) return `Loading job ${pk}`;
  if (data?.job_data?.jid) return `Job ${data.job_data.jid}`;
  return pk ? `Job ${pk}` : "Job detail";
}

function renderJobEntityLink(value, to, fallbackText) {
  return value ? <Link to={to}>{value}</Link> : fallbackText;
}

const PlotPanel = memo(function PlotPanel({
  item,
  id,
  plotName,
  unavailableReason,
  isLoading,
}) {
  const plotDescId = `${id}-plot-desc`;
  return (
    <div className="job-detail-plot-embed-host">
      <p id={plotDescId} className="visually-hidden">
        Interactive performance chart. Scales to the available width. Numerical detail may not be read
        by assistive technology.
      </p>
      <BokehEmbed
        item={item}
        id={id}
        plotName={plotName}
        unavailableReason={unavailableReason}
        isLoadingExternal={isLoading}
        wrapperClassName="job-detail-plot-embed"
        ariaDescribedBy={plotDescId}
        maximizeInContainer="width"
      />
    </div>
  );
});

const JOB_PLOT_CONFIGS = [
  {
    key: "summary_plot",
    panelKey: "summary",
    idPrefix: "job-mscript",
    plotName: "Summary plot",
  },
  {
    key: "roofline",
    panelKey: "roofline-cpu",
    idPrefix: "job-roofline",
    plotName: "CPU Roofline",
  },
  {
    key: "gpu_roofline",
    panelKey: "roofline-gpu",
    idPrefix: "job-gpu-roofline",
    plotName: "GPU Roofline (PCIe/NvLink)",
  },
];

function createEmptyJobPlotsState(loading) {
  return JOB_PLOT_CONFIGS.reduce((acc, config) => {
    acc[config.key] = {
      loading,
      plotItem: null,
      unavailableReason: null,
    };
    return acc;
  }, {});
}

/** Maps React plot keys to `job_plots` batch payload fields (plot=all). */
const JOB_PLOTS_BATCH_FIELDS = {
  summary_plot: { item: "mplot_item", reason: "mplot_unavailable_reason" },
  roofline: { item: "rplot_item", reason: "rplot_unavailable_reason" },
  gpu_roofline: { item: "grplot_item", reason: "grplot_unavailable_reason" },
};

function plotsStateFromBatchResponse(resp) {
  return JOB_PLOT_CONFIGS.reduce((acc, config) => {
    const fields = JOB_PLOTS_BATCH_FIELDS[config.key];
    acc[config.key] = {
      loading: false,
      plotItem: resp[fields.item] ?? null,
      unavailableReason: resp[fields.reason] ?? null,
    };
    return acc;
  }, {});
}

/** Merge a progressive `job_plots` partial payload into existing per-plot state. */
export function mergeProgressiveJobPlotsState(prevPlots, resp) {
  const loadingSet = new Set(resp.loading_plots ?? []);
  return JOB_PLOT_CONFIGS.reduce((acc, config) => {
    const fields = JOB_PLOTS_BATCH_FIELDS[config.key];
    const previous = prevPlots?.[config.key] ?? {
      loading: true,
      plotItem: null,
      unavailableReason: null,
    };
    if (loadingSet.has(config.key)) {
      acc[config.key] = {
        loading: true,
        plotItem: previous.plotItem,
        unavailableReason: previous.unavailableReason,
      };
      return acc;
    }
    if (Object.hasOwn(resp, fields.item)) {
      acc[config.key] = {
        loading: false,
        plotItem: resp[fields.item] ?? null,
        unavailableReason: resp[fields.reason] ?? null,
      };
      return acc;
    }
    acc[config.key] = { ...previous, loading: true };
    return acc;
  }, {});
}

export function jobPlotEntryEqual(p, q) {
  if (p === q) return true;
  if (!p || !q) return false;
  if (p.loading !== q.loading || p.unavailableReason !== q.unavailableReason) return false;
  if (p.plotItem === q.plotItem) return true;
  if (p.plotItem == null && q.plotItem == null) return true;
  if (p.plotItem == null || q.plotItem == null) return false;
  try {
    return JSON.stringify(p.plotItem) === JSON.stringify(q.plotItem);
  } catch {
    return false;
  }
}

export function jobPlotStatesEqual(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;
  return JOB_PLOT_CONFIGS.every((cfg) => jobPlotEntryEqual(a[cfg.key], b[cfg.key]));
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
  const [analysisTab, setAnalysisTab] = useState("metrics");
  const tabMetricsId = useId();
  const tabProcessesId = useId();
  const tabExecHostsId = useId();
  const tabDeviceId = useId();
  const tabPlotSummaryId = useId();
  const tabPlotRooflineId = useId();
  const tabMultiprecisionMixId = useId();

  const plotTabDomIds = {
    summary: tabPlotSummaryId,
    roofline: tabPlotRooflineId,
  };

  useDocumentTitle(buildJobDetailTitle({ error, loading, data, pk }));

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

        setPlots(createEmptyJobPlotsState(true));
        setPlotsLoading(true);

        const fetchAllJobPlotsWithPolling = async () => {
          let keepLoading = false;
          try {
            const plotResponse = await api.getJobPlots(pk, null, false, true);
            if (cancelled) return;

            if (plotResponse?.status === "loading") {
              keepLoading = true;
              scheduleJobPlotsRetry(
                fetchAllJobPlotsWithPolling,
                plotResponse.retry_after_seconds,
                () => cancelled
              );
              return;
            }

            if (plotResponse?.status === "partial" && plotResponse?.progressive) {
              keepLoading = true;
              setPlots((prev) => {
                const merged = mergeProgressiveJobPlotsState(prev, plotResponse);
                return jobPlotStatesEqual(prev, merged) ? prev : merged;
              });
              scheduleJobPlotsRetry(
                fetchAllJobPlotsWithPolling,
                plotResponse.retry_after_seconds,
                () => cancelled
              );
              return;
            }

            if (
              plotResponse &&
              typeof plotResponse === "object" &&
              Object.hasOwn(plotResponse, "mplot_item")
            ) {
              setPlots((prev) => {
                const next = plotsStateFromBatchResponse(plotResponse);
                return jobPlotStatesEqual(prev, next) ? prev : next;
              });
            } else {
              setPlots(createEmptyJobPlotsState(false));
            }
          } catch {
            if (cancelled) return;
            // eslint-disable-next-line no-console
            console.warn(`Failed to load job plots for job ${pk}`);
            setPlots(createEmptyJobPlotsState(false));
          } finally {
            if (cancelled || keepLoading) return;
          }
        };

        fetchAllJobPlotsWithPolling();

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
    const anyPlotReady = JOB_PLOT_CONFIGS.some(
      (config) => plots?.[config.key] && plots[config.key].loading === false
    );
    if (anyPlotReady) setPlotsLoading(false);
  }, [plots]);

  if (loading) {
    return (
      <div className="job-detail-skeleton" aria-busy="true">
        <span className="visually-hidden" role="status" aria-label="Loading job detail">
          Loading job detail
        </span>
        <div className="placeholder-glow mb-3">
          <span className="placeholder col-6" style={{ height: "2.5rem" }} />
        </div>
        <div
          className="job-detail-skeleton-plot border rounded p-2 mb-4"
          aria-hidden="true"
        >
          <div className="placeholder-glow">
            <span className="placeholder col-8" />
          </div>
          <div
            className="mt-2 placeholder-glow rounded w-100"
            style={{ minHeight: "320px", background: "#e9ecef" }}
          />
        </div>
      </div>
    );
  }
  if (error) return <BannerErrorMessage message={error} />;
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
    multiprecision_cpu_plot_item,
    multiprecision_cpu_unavailable_reason,
    multiprecision_gpu_plot_item,
    multiprecision_gpu_unavailable_reason,
    metrics_list = [],
    proc_list = [],
    staff_metrics_distinct_time_count: staffMetricsDistinctTimeCount,
  } = data;

  const gpuStatsTableCellStyle = {
    label: { border: "1px solid lightgrey" },
    value: { border: "1px solid lightgrey", textAlign: "right" },
  };
  const gpuStatsRows = [
    {
      key: "gpu_count",
      label: "Total GPUs allocated:",
      value: formatDecimalStandard(gpu_count),
    },
    {
      key: "gpu_active",
      label: "Number of GPUs active:",
      value: formatDecimalStandard(gpu_active),
    },
    {
      key: "gpu_util_max",
      label: "Max GPU Utilization:",
      value:
        gpu_utilization_max != null && gpu_utilization_max !== ""
          ? `${formatDecimalStandard(gpu_utilization_max)}%`
          : "",
    },
    {
      key: "gpu_util_mean",
      label: "Mean GPU Utilization:",
      value:
        gpu_utilization_mean != null && gpu_utilization_mean !== ""
          ? `${formatDecimalStandard(gpu_utilization_mean)}%`
          : "",
    },
  ];

  const hasDeviceData = Object.keys(schema).length > 0;
  const plotConfigByKey = JOB_PLOT_CONFIGS.reduce((acc, config) => {
    acc[config.key] = config;
    return acc;
  }, {});
  const plotPanels = JOB_PLOT_CONFIGS.map((config) => ({
    key: config.panelKey,
    item: plots?.[config.key]?.plotItem ?? null,
    isLoading: !!plots?.[config.key]?.loading,
    id: `${config.idPrefix}-${pk}`,
    plotName: config.plotName,
    unavailableReason: plots?.[config.key]?.unavailableReason ?? null,
  }));

  const metricsListFull = metrics_list || [];
  const metricsSplitIdx = Math.ceil(metricsListFull.length / 2);
  const metricsTableLeft = metricsListFull.slice(0, metricsSplitIdx);
  const metricsTableRight = metricsListFull.slice(metricsSplitIdx);

  function metricTableRows(list) {
    return list.map((obj) => (
      <tr key={obj.metric}>
        <th scope="row">
          <VariableInfoLabel
            variableName={obj.metric}
            labelText={getJobMetricShortLabel(obj.metric)}
            enableHelp
          />{" "}
          [{obj.units}]
        </th>
        <td className={obj.value != null && obj.value !== "" ? "" : "text-muted"}>
          {formatJobMetricCell(obj, isStaff)}
        </td>
      </tr>
    ));
  }

  function renderSinglePlotPanel(config, isTabActive) {
    if (!config) return null;
    const panel = plotPanels.find((p) => p.key === config.panelKey);
    if (!panel) return null;
    return (
      <div key={config.key} className="job-detail-single-plot-host w-100 mb-3">
        <h3 className="h6">{config.plotName}</h3>
        <p className="job-detail-plots-intro text-muted small mb-2">
          Host-level plot for this job. Loads progressively; chart width follows the panel below.
        </p>
        {isTabActive ? (
          <PlotPanel
            item={panel.item}
            id={panel.id}
            plotName={panel.plotName}
            unavailableReason={panel.unavailableReason}
            isLoading={panel.isLoading}
          />
        ) : null}
      </div>
    );
  }

  return (
    <>
      <h1 className="h2 mb-3">Job {job.jid}</h1>

      <section id="job-detail-glance" className="mb-4" aria-labelledby="job-detail-glance-heading">
        <h2 id="job-detail-glance-heading" className="h5">
          Job overview
        </h2>
        <div className="card mb-0">
          <div className="card-body">
            <div className="row row-cols-1 row-cols-sm-2 row-cols-lg-3 g-3 small">
              <div>
                <div className="text-muted">Job ID</div>
                <div>
                  <Link to={`/job/${job.jid}`}>{job.jid}</Link>
                </div>
              </div>
              <div>
                <div className="text-muted">Status</div>
                <div>{job.state}</div>
              </div>
              <div>
                <div className="text-muted">Run time (s)</div>
                <div>{formatDecimalStandard(job.runtime)}</div>
              </div>
              <div>
                <div className="text-muted">Queue</div>
                <div>
                  {renderJobEntityLink(
                    job.queue,
                    `/queue/${encodeURIComponent(job.queue)}/`,
                    ""
                  )}
                </div>
              </div>
              <div>
                <div className="text-muted">User</div>
                <div>
                  {renderJobEntityLink(job.username, `/username/${job.username}/`, "Unknown")}
                </div>
              </div>
              <div>
                <div className="text-muted">Project</div>
                <div>
                  {renderJobEntityLink(job.account, `/account/${job.account}/`, "None")}
                </div>
              </div>
              <div>
                <div className="text-muted">Cores / nodes</div>
                <div>
                  {formatDecimalStandard(job.ncores)} / {formatDecimalStandard(job.nhosts)}
                </div>
              </div>
              <div>
                <div className="text-muted">Start</div>
                <div>{formatDateTime(job.start_time)}</div>
              </div>
              <div>
                <div className="text-muted">End</div>
                <div>{formatDateTime(job.end_time)}</div>
              </div>
              <div className="col-12">
                <div className="text-muted">Job name</div>
                <div>{job.jobname}</div>
              </div>
              {isStaff ? (
                <div className="col-12">
                  <div className="text-muted">
                    <VariableInfoLabel
                      variableName="metrics_distinct_time_count"
                      labelText="Sample Count"
                      enableHelp
                    />
                  </div>
                  <div>
                    {staffMetricsDistinctTimeCount != null && staffMetricsDistinctTimeCount !== ""
                      ? formatDecimalStandard(staffMetricsDistinctTimeCount)
                      : "Not computed yet."}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      <section id="job-detail-scheduling" className="mb-4" aria-labelledby="job-detail-scheduling-heading">
        <h2 id="job-detail-scheduling-heading" className="visually-hidden">
          Full scheduling record
        </h2>
        <details className="job-detail-scheduling-details border rounded px-3 py-2">
          <summary className="fw-semibold">
            Full scheduling record
            <span className="text-muted small fw-normal"> — all accounting columns</span>
          </summary>
          <div className="table-responsive mt-2">
            <table className="table table-sm table-bordered">
              <thead>
                <tr>
                  <th>
                    <VariableInfoLabel variableName="jid" labelText="Job ID" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="username" labelText="user" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="account" labelText="project" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="start_time" labelText="start time" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="end_time" labelText="end time" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="runtime" labelText="run time (s)" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="timelimit" labelText="requested time (s)" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="queue" labelText="queue" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="jobname" labelText="name" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="state" labelText="status" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="ncores" labelText="ncores" enableHelp />
                  </th>
                  <th>
                    <VariableInfoLabel variableName="nhosts" labelText="nnodes" enableHelp />
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr style={{ backgroundColor: job.color || "#fff" }}>
                  <td>
                    <Link to={`/job/${job.jid}`}>{job.jid}</Link>
                  </td>
                  <td>
                    {renderJobEntityLink(job.username, `/username/${job.username}/`, "Unknown")}
                  </td>
                  <td>
                    {renderJobEntityLink(job.account, `/account/${job.account}/`, "None")}
                  </td>
                  <td>{formatDateTime(job.start_time)}</td>
                  <td>{formatDateTime(job.end_time)}</td>
                  <td>{formatDecimalStandard(job.runtime)}</td>
                  <td>{formatDecimalStandard(job.timelimit)}</td>
                  <td>
                    {renderJobEntityLink(
                      job.queue,
                      `/queue/${encodeURIComponent(job.queue)}/`,
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
        </details>
      </section>

      <section id="job-detail-resources" className="mb-4" aria-labelledby="job-detail-resources-heading">
        <h2 id="job-detail-resources-heading" className="h5">
          Resources
        </h2>
        <div className="row">
          <div className="col-lg-8">
            <div className="table-responsive">
              <table className="table table-sm table-bordered">
                <thead>
                  <tr>
                    <th>Shared File System</th>
                    <th>MB Read</th>
                    <th>MB Written</th>
                  </tr>
                </thead>
                <tbody>
                  {detailsLoading ? (
                    <tr>
                      <td colSpan={3} className="text-muted">
                        Loading shared file system data…
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
        <div className="d-flex flex-wrap gap-2 mt-2">
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
          <div className="mt-3">
            {detailsLoading && gpu_active == null && gpu_count == null ? (
              <p className="text-muted mb-0" role="status">
                Loading GPU statistics…
              </p>
            ) : (
              <table border="1" className="mb-0">
                <tbody>
                  {gpuStatsRows.map((row) => (
                    <tr key={row.key}>
                      <td style={gpuStatsTableCellStyle.label}>
                        <b>{row.label}</b>
                      </td>
                      <td style={gpuStatsTableCellStyle.value}>{row.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </section>

      <section
        id="job-detail-analysis"
        className="mb-4"
        aria-labelledby="job-detail-analysis-heading"
      >
        <h2 id="job-detail-analysis-heading" className="h5">
          Job data
        </h2>
        <ul
          className="nav nav-tabs flex-wrap job-detail-analysis-tabs mb-0"
          role="tablist"
          aria-label="Job data views"
        >
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${analysisTab === "metrics" ? "active" : ""}`}
              id={tabMetricsId}
              role="tab"
              aria-selected={analysisTab === "metrics"}
              aria-controls="job-detail-panel-metrics"
              tabIndex={analysisTab === "metrics" ? 0 : -1}
              onClick={() => setAnalysisTab("metrics")}
            >
              Metrics
            </button>
          </li>
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${analysisTab === "summary" ? "active" : ""}`}
              id={plotTabDomIds.summary}
              role="tab"
              aria-selected={analysisTab === "summary"}
              aria-controls="job-detail-panel-plot-summary"
              tabIndex={analysisTab === "summary" ? 0 : -1}
              onClick={() => setAnalysisTab("summary")}
            >
              Summary plot
            </button>
          </li>
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${analysisTab === "roofline" ? "active" : ""}`}
              id={plotTabDomIds.roofline}
              role="tab"
              aria-selected={analysisTab === "roofline"}
              aria-controls="job-detail-panel-plot-roofline"
              tabIndex={analysisTab === "roofline" ? 0 : -1}
              onClick={() => setAnalysisTab("roofline")}
            >
              Roofline
            </button>
          </li>
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${analysisTab === "multiprecisionMix" ? "active" : ""}`}
              id={tabMultiprecisionMixId}
              role="tab"
              aria-selected={analysisTab === "multiprecisionMix"}
              aria-controls="job-detail-panel-multiprecision-mix"
              tabIndex={analysisTab === "multiprecisionMix" ? 0 : -1}
              onClick={() => setAnalysisTab("multiprecisionMix")}
            >
              Multiprecision Mix
            </button>
          </li>
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${analysisTab === "processes" ? "active" : ""}`}
              id={tabProcessesId}
              role="tab"
              aria-selected={analysisTab === "processes"}
              aria-controls="job-detail-panel-processes"
              tabIndex={analysisTab === "processes" ? 0 : -1}
              onClick={() => setAnalysisTab("processes")}
            >
              Processes
            </button>
          </li>
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${analysisTab === "execHosts" ? "active" : ""}`}
              id={tabExecHostsId}
              role="tab"
              aria-selected={analysisTab === "execHosts"}
              aria-controls="job-detail-panel-exec-hosts"
              tabIndex={analysisTab === "execHosts" ? 0 : -1}
              onClick={() => setAnalysisTab("execHosts")}
            >
              Execution and hosts
            </button>
          </li>
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${analysisTab === "device" ? "active" : ""}`}
              id={tabDeviceId}
              role="tab"
              aria-selected={analysisTab === "device"}
              aria-controls="job-detail-panel-device"
              tabIndex={analysisTab === "device" ? 0 : -1}
              onClick={() => setAnalysisTab("device")}
            >
              Device data
            </button>
          </li>
        </ul>
        <div className="job-detail-analysis-panel border border-top-0 rounded-bottom p-3 bg-body">
          {plotsLoading ? (
            <p className="text-muted small mb-2" role="status">
              Loading job plots…
            </p>
          ) : null}
          <div
            id="job-detail-panel-plot-summary"
            role="tabpanel"
            aria-labelledby={plotTabDomIds.summary}
            className="job-detail-single-plot-pane"
            hidden={analysisTab !== "summary"}
          >
            {renderSinglePlotPanel(
              plotConfigByKey.summary_plot,
              analysisTab === "summary",
            )}
          </div>
          <div
            id="job-detail-panel-plot-roofline"
            role="tabpanel"
            aria-labelledby={plotTabDomIds.roofline}
            className="job-detail-single-plot-pane"
            hidden={analysisTab !== "roofline"}
          >
            {renderSinglePlotPanel(
              plotConfigByKey.roofline,
              analysisTab === "roofline",
            )}
            {renderSinglePlotPanel(
              plotConfigByKey.gpu_roofline,
              analysisTab === "roofline",
            )}
          </div>
          <div
            id="job-detail-panel-metrics"
            role="tabpanel"
            aria-labelledby={tabMetricsId}
            hidden={analysisTab !== "metrics"}
          >
            {detailsLoading ? (
              <p className="text-muted mb-0">Loading job-level metrics…</p>
            ) : !metrics_list.length ? (
              <p className="text-muted mb-0">Data not available.</p>
            ) : metricsTableRight.length === 0 ? (
              <div className="table-responsive">
                <table className="table table-sm table-bordered job-detail-metrics-table mb-0">
                  <tbody>{metricTableRows(metricsTableLeft)}</tbody>
                </table>
              </div>
            ) : (
              <div className="row g-3 job-detail-metrics-two-col">
                <div className="col-12 col-lg-6">
                  <div className="table-responsive">
                    <table className="table table-sm table-bordered job-detail-metrics-table mb-0">
                      <tbody>{metricTableRows(metricsTableLeft)}</tbody>
                    </table>
                  </div>
                </div>
                <div className="col-12 col-lg-6">
                  <div className="table-responsive">
                    <table className="table table-sm table-bordered job-detail-metrics-table mb-0">
                      <tbody>{metricTableRows(metricsTableRight)}</tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}
          </div>
          <div
            id="job-detail-panel-multiprecision-mix"
            role="tabpanel"
            aria-labelledby={tabMultiprecisionMixId}
            hidden={analysisTab !== "multiprecisionMix"}
          >
            <div className="row g-3">
              <div className="col-12 col-lg-6">
                <div className="job-detail-single-plot-host w-100 mb-3">
                  <h3 className="h6">CPU Multiprecision Mix</h3>
                  <p className="job-detail-plots-intro text-muted small mb-2">
                    Host-level plot for this job. Loads progressively; chart width follows the panel
                    below.
                  </p>
                  {analysisTab === "multiprecisionMix" ? (
                    <PlotPanel
                      item={multiprecision_cpu_plot_item}
                      id={`job-multiprecision-cpu-${pk}`}
                      plotName="CPU Multiprecision Mix"
                      unavailableReason={multiprecision_cpu_unavailable_reason}
                      isLoading={false}
                    />
                  ) : null}
                </div>
              </div>
              <div className="col-12 col-lg-6">
                <div className="job-detail-single-plot-host w-100 mb-3">
                  <h3 className="h6">GPU Multiprecision Mix</h3>
                  <p className="job-detail-plots-intro text-muted small mb-2">
                    Host-level plot for this job. Loads progressively; chart width follows the panel
                    below.
                  </p>
                  {analysisTab === "multiprecisionMix" ? (
                    <PlotPanel
                      item={multiprecision_gpu_plot_item}
                      id={`job-multiprecision-gpu-${pk}`}
                      plotName="GPU Multiprecision Mix"
                      unavailableReason={multiprecision_gpu_unavailable_reason}
                      isLoading={false}
                    />
                  ) : null}
                </div>
              </div>
            </div>
          </div>
          <div
            id="job-detail-panel-processes"
            role="tabpanel"
            aria-labelledby={tabProcessesId}
            hidden={analysisTab !== "processes"}
          >
            {detailsLoading ? (
              <p className="text-muted mb-0">Loading processes…</p>
            ) : !(proc_list || []).length ? (
              <p className="text-muted mb-0">Data not available.</p>
            ) : (
              <div className="table-responsive">
                <table className="table table-sm table-bordered">
                  <tbody>
                    {(proc_list || []).map((proc, i) => (
                      <tr key={i}>
                        <td>{proc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div
            id="job-detail-panel-exec-hosts"
            role="tabpanel"
            aria-labelledby={tabExecHostsId}
            hidden={analysisTab !== "execHosts"}
          >
            <h3 className="h6">Execution parameters</h3>
            {detailsLoading ? (
              <p className="text-muted">Loading execution parameters…</p>
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
                            <span key={`exec-${i}`}>
                              {item}
                              <br />
                            </span>
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
                            <span key={`cwd-${i}`}>
                              {item}
                              <br />
                            </span>
                          ))
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
                <table className="table table-sm table-bordered mt-2">
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
            <h3 className="h6 mt-3">Hosts</h3>
            {!host_list.length ? (
              <p className="text-muted mb-0">Data not available.</p>
            ) : (
              <div className="table-responsive">
                <table className="table table-sm table-bordered">
                  <tbody>
                    {host_list.map((host, i) => (
                      <tr key={i}>
                        <td>{host}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div
            id="job-detail-panel-device"
            role="tabpanel"
            aria-labelledby={tabDeviceId}
            hidden={analysisTab !== "device"}
          >
            <div className="text-center text-md-start">
              {detailsLoading ? (
                <p className="text-muted mb-0" role="status">
                  Loading device data and plots…
                </p>
              ) : !hasDeviceData ? (
                <p className="text-muted mb-0" role="status">
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
                            {Array.isArray(event)
                              ? event.map((ev, i) => (
                                  <span key={ev}>
                                    {i > 0 ? ", " : ""}
                                    <VariableInfoLabel
                                      variableName={ev}
                                      labelText={ev}
                                      enableHelp
                                    />
                                  </span>
                                ))
                              : event}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
