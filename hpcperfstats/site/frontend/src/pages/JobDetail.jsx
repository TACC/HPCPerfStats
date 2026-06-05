import { useCallback, useEffect, memo, useId, useMemo, useRef, useState } from "react";
import { useParams, Link, useNavigate, useSearchParams, useLocation } from "react-router-dom";
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
import PageBreadcrumbs from "../components/PageBreadcrumbs";
import { getJobMetricShortLabel } from "../utils/jobMetricDisplayLabels";
import {
  readTabFromSearchParams,
  searchParamsWithTab,
} from "../utils/sync-tab-search-param";
import { useArrowKeyTabs } from "../hooks/useArrowKeyTabs";

const JOB_DETAIL_ANALYSIS_TABS = new Set([
  "metrics",
  "summary",
  "roofline",
  "multiprecisionMix",
  "processes",
  "execHosts",
  "device",
]);

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
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [data, setData] = useState(null);
  const [plots, setPlots] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [plotsLoading, setPlotsLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailFetchWarning, setDetailFetchWarning] = useState(false);
  const [plotsFetchFailed, setPlotsFetchFailed] = useState(false);
  const plotsFetchGenRef = useRef(0);
  const rawTab = readTabFromSearchParams(searchParams, "tab", "metrics");
  const analysisTab = JOB_DETAIL_ANALYSIS_TABS.has(rawTab) ? rawTab : "metrics";

  function setAnalysisTab(tab) {
    const next = searchParamsWithTab(
      searchParams,
      "tab",
      tab === "metrics" ? null : tab,
    );
    const qs = next.toString();
    navigate(qs ? `${location.pathname}?${qs}` : location.pathname, { replace: true });
  }
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

  const analysisTabButtonIds = useMemo(
    () => [
      tabMetricsId,
      tabPlotSummaryId,
      tabPlotRooflineId,
      tabMultiprecisionMixId,
      tabProcessesId,
      tabExecHostsId,
      tabDeviceId,
    ],
    [
      tabMetricsId,
      tabPlotSummaryId,
      tabPlotRooflineId,
      tabMultiprecisionMixId,
      tabProcessesId,
      tabExecHostsId,
      tabDeviceId,
    ],
  );

  const analysisTabIdToKey = useMemo(
    () => ({
      [tabMetricsId]: "metrics",
      [tabPlotSummaryId]: "summary",
      [tabPlotRooflineId]: "roofline",
      [tabMultiprecisionMixId]: "multiprecisionMix",
      [tabProcessesId]: "processes",
      [tabExecHostsId]: "execHosts",
      [tabDeviceId]: "device",
    }),
    [
      tabMetricsId,
      tabPlotSummaryId,
      tabPlotRooflineId,
      tabMultiprecisionMixId,
      tabProcessesId,
      tabExecHostsId,
      tabDeviceId,
    ],
  );

  const activeAnalysisTabButtonId = useMemo(() => {
    const entry = Object.entries(analysisTabIdToKey).find(([, key]) => key === analysisTab);
    return entry ? entry[0] : tabMetricsId;
  }, [analysisTab, analysisTabIdToKey, tabMetricsId]);

  const handleAnalysisTabKeyDown = useArrowKeyTabs(
    analysisTabButtonIds,
    activeAnalysisTabButtonId,
    (nextTabButtonId) => {
      const nextKey = analysisTabIdToKey[nextTabButtonId];
      if (nextKey) setAnalysisTab(nextKey);
    },
  );

  useDocumentTitle(buildJobDetailTitle({ error, loading, data, pk }));

  const fetchAllJobPlotsWithPolling = useCallback(
    async (cancelledCheck) => {
      let keepLoading = false;
      try {
        const plotResponse = await api.getJobPlots(pk, null, false, true);
        if (cancelledCheck()) return;

        if (plotResponse?.status === "loading") {
          keepLoading = true;
          scheduleJobPlotsRetry(
            () => fetchAllJobPlotsWithPolling(cancelledCheck),
            plotResponse.retry_after_seconds,
            cancelledCheck,
          );
          return;
        }

        if (plotResponse?.status === "partial" && plotResponse?.progressive) {
          keepLoading = true;
          setPlotsFetchFailed(false);
          setPlots((prev) => {
            const merged = mergeProgressiveJobPlotsState(prev, plotResponse);
            return jobPlotStatesEqual(prev, merged) ? prev : merged;
          });
          scheduleJobPlotsRetry(
            () => fetchAllJobPlotsWithPolling(cancelledCheck),
            plotResponse.retry_after_seconds,
            cancelledCheck,
          );
          return;
        }

        if (
          plotResponse &&
          typeof plotResponse === "object" &&
          Object.hasOwn(plotResponse, "mplot_item")
        ) {
          setPlotsFetchFailed(false);
          setPlots((prev) => {
            const next = plotsStateFromBatchResponse(plotResponse);
            return jobPlotStatesEqual(prev, next) ? prev : next;
          });
        } else {
          setPlots(createEmptyJobPlotsState(false));
        }
      } catch {
        if (cancelledCheck()) return;
        setPlotsFetchFailed(true);
        setPlots(createEmptyJobPlotsState(false));
      } finally {
        if (cancelledCheck() || keepLoading) return;
        setPlotsLoading(false);
      }
    },
    [pk],
  );

  const retryJobPlots = useCallback(() => {
    setPlotsFetchFailed(false);
    setPlotsLoading(true);
    setPlots(createEmptyJobPlotsState(true));
    plotsFetchGenRef.current += 1;
    const gen = plotsFetchGenRef.current;
    void fetchAllJobPlotsWithPolling(() => plotsFetchGenRef.current !== gen);
  }, [fetchAllJobPlotsWithPolling]);

  useEffect(() => {
    if (!pk) return;

    let cancelled = false;
    const cancelledCheck = () => cancelled;

    setError(null);
    setData(null);
    setPlots(null);
    setLoading(true);
    setPlotsLoading(true);
    setDetailsLoading(false);
    setDetailFetchWarning(false);
    setPlotsFetchFailed(false);

    api
      .getJobDetailLight(pk)
      .then((jobLightData) => {
        if (cancelled) return;
        setData(jobLightData);
        setLoading(false);
        setDetailsLoading(true);
        setPlots(createEmptyJobPlotsState(true));
        setPlotsLoading(true);
        void fetchAllJobPlotsWithPolling(cancelledCheck);

        api
          .getJobDetail(pk)
          .then((jobFullData) => {
            if (cancelled) return;
            setData(jobFullData);
            setDetailFetchWarning(false);
          })
          .catch(() => {
            if (cancelled) return;
            setDetailFetchWarning(true);
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
  }, [pk, fetchAllJobPlotsWithPolling]);

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
      <PageBreadcrumbs
        items={[
          { label: "Browse", to: "/" },
          { label: `Job ${job.jid}` },
        ]}
      />
      <h1 className="h2 mb-3">Job {job.jid}</h1>
      {detailFetchWarning ? (
        <div className="alert alert-warning small" role="status">
          Some job details could not be loaded. Showing partial data from a quick load.
        </div>
      ) : null}

      <section id="job-detail-glance" className="mb-4" aria-labelledby="job-detail-glance-heading">
        <h2 id="job-detail-glance-heading" className="h5">
          Job overview
        </h2>
        <div className="card mb-0">
          <div className="card-body">
            <div className="row row-cols-1 row-cols-sm-2 row-cols-lg-3 g-3 small">
              <div>
                <div className="text-muted">Job ID</div>
                <div>{job.jid}</div>
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
              <caption className="visually-hidden">
                Full scheduling record for job {job.jid}
              </caption>
              <thead>
                <tr>
                  <th scope="col">
                    <VariableInfoLabel variableName="jid" labelText="Job ID" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="username" labelText="user" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="account" labelText="project" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="start_time" labelText="start time" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="end_time" labelText="end time" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="runtime" labelText="run time (s)" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="timelimit" labelText="requested time (s)" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="queue" labelText="queue" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="jobname" labelText="name" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="state" labelText="status" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="ncores" labelText="ncores" enableHelp />
                  </th>
                  <th scope="col">
                    <VariableInfoLabel variableName="nhosts" labelText="nnodes" enableHelp />
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr>
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
                <caption className="visually-hidden">
                  Shared file system I/O for job {job.jid}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Shared File System</th>
                    <th scope="col">MB Read</th>
                    <th scope="col">MB Written</th>
                    <th scope="col">Peak MB/s</th>
                    <th scope="col">Peak IOPS</th>
                  </tr>
                </thead>
                <tbody>
                  {detailsLoading ? (
                    <tr>
                      <td colSpan={5} className="text-muted">
                        Loading shared file system data…
                      </td>
                    </tr>
                  ) : Object.keys(fsio).length === 0 ? (
                    <tr>
                      <td colSpan={5} className="text-muted">
                        Data not available.
                      </td>
                    </tr>
                  ) : (
                    Object.entries(fsio).map(([key, val]) => (
                      <tr key={key}>
                        <td>{key}</td>
                        <td>{formatDecimalStandard(val[0])}</td>
                        <td>{formatDecimalStandard(val[1])}</td>
                        <td>
                          {val[2] != null && !Number.isNaN(val[2])
                            ? formatDecimalStandard(val[2])
                            : "—"}
                        </td>
                        <td>
                          {val[3] != null && !Number.isNaN(val[3])
                            ? formatDecimalStandard(val[3])
                            : "—"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
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
          className="nav nav-tabs job-detail-analysis-tabs job-detail-tab-scroll mb-0"
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
              onKeyDown={(e) => handleAnalysisTabKeyDown(e, tabMetricsId)}
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
              onKeyDown={(e) => handleAnalysisTabKeyDown(e, plotTabDomIds.summary)}
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
              onKeyDown={(e) => handleAnalysisTabKeyDown(e, plotTabDomIds.roofline)}
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
              onKeyDown={(e) => handleAnalysisTabKeyDown(e, tabMultiprecisionMixId)}
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
              onKeyDown={(e) => handleAnalysisTabKeyDown(e, tabProcessesId)}
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
              onKeyDown={(e) => handleAnalysisTabKeyDown(e, tabExecHostsId)}
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
              onKeyDown={(e) => handleAnalysisTabKeyDown(e, tabDeviceId)}
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
          {plotsFetchFailed ? (
            <div className="alert alert-warning small py-2" role="alert">
              <p className="mb-2">Job plots could not be loaded. The table and metrics below are unchanged.</p>
              <button type="button" className="btn btn-outline-secondary btn-sm" onClick={retryJobPlots}>
                Retry plots
              </button>
            </div>
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
            <p className="text-muted small mb-2">CPU and GPU roofline charts for this job.</p>
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
                  <caption className="visually-hidden">Job-level metrics for job {job.jid}</caption>
                  <tbody>{metricTableRows(metricsTableLeft)}</tbody>
                </table>
              </div>
            ) : (
              <div className="row g-3 job-detail-metrics-two-col">
                <div className="col-12 col-lg-6">
                  <div className="table-responsive">
                    <table className="table table-sm table-bordered job-detail-metrics-table mb-0">
                      <caption className="visually-hidden">
                        Job-level metrics for job {job.jid} (column 1)
                      </caption>
                      <tbody>{metricTableRows(metricsTableLeft)}</tbody>
                    </table>
                  </div>
                </div>
                <div className="col-12 col-lg-6">
                  <div className="table-responsive">
                    <table className="table table-sm table-bordered job-detail-metrics-table mb-0">
                      <caption className="visually-hidden">
                        Job-level metrics for job {job.jid} (column 2)
                      </caption>
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
            className="job-detail-single-plot-pane"
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
                      isLoading={
                        detailsLoading &&
                        !multiprecision_cpu_plot_item &&
                        !multiprecision_cpu_unavailable_reason
                      }
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
                      isLoading={
                        detailsLoading &&
                        !multiprecision_gpu_plot_item &&
                        !multiprecision_gpu_unavailable_reason
                      }
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
                  <caption className="visually-hidden">
                    Processes recorded for job {job.jid}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Process</th>
                    </tr>
                  </thead>
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
                  <caption className="visually-hidden">
                    Execution parameters for job {job.jid}
                  </caption>
                  <tbody>
                    <tr>
                      <th scope="row">Executable Path</th>
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
                      <th scope="row">Working Directory</th>
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
                  <caption className="visually-hidden">
                    Modules and libraries for job {job.jid}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Module</th>
                      <th scope="col">Library</th>
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
                  <caption className="visually-hidden">
                    Execution hosts for job {job.jid}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Host</th>
                    </tr>
                  </thead>
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
                    <caption className="visually-hidden">
                      Device types and events for job {job.jid}
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Type Name</th>
                        <th scope="col">Recorded Performance Events</th>
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
