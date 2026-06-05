import { useId, useState } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage.jsx";
import BokehPlotWithLimitation from "../components/BokehPlotWithLimitation.jsx";
import LoadingMessage from "../components/LoadingMessage.jsx";
import { usePubDashboardBundle } from "../pub-dashboard-bundle-context.js";
import { useDocumentTitle } from "../utils/useDocumentTitle.js";
import { formatDecimalStandard } from "../utils/formatDecimal.js";

const CLUSTER_DASH_TAB_EXPANSION = "expansion-factors";

function SectionExpansionFactor({ bundle }) {
  const efSection = bundle?.sections?.expansion_factor || {};
  const monthly = efSection.monthly_daily_histograms || {};
  const yearly = efSection.yearly_weekly_histograms || {};
  const panelIntroId = useId();

  const renderHist = (payloadMap, axisHint, groupingKey, histogramCaption) => {
    const keys = Object.keys(payloadMap).sort().reverse();
    if (!keys.length) {
      return (
        <p className="text-muted mb-0">
          No histogram data for this grouping yet.
        </p>
      );
    }
    return (
      <div className="mb-4">
        <p className="small text-muted mb-2">{histogramCaption}</p>
        <p className="small text-muted mb-3">{axisHint}</p>
        {keys.map((k) => {
          const block = payloadMap[k];
          const safeDomId = String(k).replace(/[^a-zA-Z0-9_-]+/g, "-");
          const edges = block.histogram_bin_edges || [];
          const counts = block.histogram_counts || [];
          const maxCount = counts.length
            ? Math.max(1, ...counts.map((c) => Number(c) || 0))
            : 1;
          return (
            <div key={k} className="mb-4 border rounded p-3 bg-light">
              <div className="fw-semibold mb-2">{k}</div>
              <div className="small text-muted mb-2">
                definition: {block.expansion_factor_definition || "—"}
              </div>
              {block.bokeh_histogram_json_item ? (
                <div className="mb-3">
                  <BokehPlotWithLimitation
                    item={block.bokeh_histogram_json_item}
                    id={`pub-expansion-factor-${groupingKey}-${safeDomId}`}
                    plotName={`Expansion factor histogram for ${k}`}
                    embedAriaLabel={`Expansion factor histogram for ${k}`}
                    embedMinHeightPx={320}
                  />
                </div>
              ) : null}
              {!block.bokeh_histogram_json_item ? (
                <div className="d-flex flex-column gap-1">
                  {(counts || []).map((cntRaw, idx) => {
                    const lo = edges[idx];
                    const hi = idx + 1 < edges.length ? edges[idx + 1] : null;
                    const cnt = Number(cntRaw) || 0;
                    const widthPct = (cnt / maxCount) * 100;
                    const labelRight =
                      hi !== null
                        ? `[${formatDecimalStandard(lo)}, ${formatDecimalStandard(hi)})`
                        : `≥ ${formatDecimalStandard(lo)}`;
                    return (
                      <div key={`${k}-${idx}`} className="d-flex align-items-center gap-2">
                        <div className="small flex-shrink-0" style={{ width: "14rem" }}>
                          {labelRight}
                        </div>
                        <div className="flex-grow-1">
                          <div className="progress" style={{ height: "1.1rem" }}>
                            <div
                              className="progress-bar"
                              role="progressbar"
                              style={{ width: `${widthPct}%` }}
                              aria-valuenow={cnt}
                              aria-valuemin={0}
                              aria-valuemax={maxCount}
                              aria-label={`${labelRight}: ${cnt} jobs`}
                            />
                          </div>
                        </div>
                        <div className="small flex-shrink-0 text-end" style={{ width: "3rem" }}>
                          {cnt}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <section aria-labelledby={panelIntroId}>
      <p id={panelIntroId} className="text-muted">
        Scheduler-centric expansion factor aggregates precomputed offline from accounting timestamps.
        Yearly views summarize distributions of{" "}
        <strong>weekly mean expansion factor</strong> values (ISO weeks). Monthly views summarize
        distributions of <strong>daily mean expansion factor</strong> values.
      </p>

      <div id="pub-dashboard-yearly" className="pt-2">
        <div className="d-flex flex-wrap align-items-baseline justify-content-between gap-2 border-bottom pb-2 mb-3">
          <h3 className="h4 mb-0">Yearly</h3>
          <a href="#pub-dashboard-monthly" className="h4 mb-0 text-decoration-underline">
            Monthly
          </a>
        </div>
        {renderHist(
          yearly,
          "Each chart lists calendar years; buckets are ISO weeks inside that year.",
          "year",
          "Histogram of weekly mean expansion factor.",
        )}
      </div>

      <hr className="my-5 border-2 opacity-50" />

      <div id="pub-dashboard-monthly">
        <div className="d-flex flex-wrap align-items-baseline justify-content-between gap-2 border-bottom pb-2 mb-3">
          <a href="#pub-dashboard-yearly" className="h4 mb-0 text-decoration-underline">
            Yearly
          </a>
          <h3 className="h4 mb-0">Monthly</h3>
        </div>
        {renderHist(
          monthly,
          "Each chart lists completed-job calendar months.",
          "month",
          "Histogram of daily mean expansion factor.",
        )}
      </div>
    </section>
  );
}

function ClusterDashboardTabs({ bundle }) {
  const [activeTab, setActiveTab] = useState(CLUSTER_DASH_TAB_EXPANSION);
  const tabExpansionId = useId();
  const panelExpansionId = useId();

  return (
    <div>
      <ul className="nav nav-tabs mb-0" role="tablist">
        <li className="nav-item" role="presentation">
          <button
            type="button"
            role="tab"
            id={tabExpansionId}
            className={`nav-link ${activeTab === CLUSTER_DASH_TAB_EXPANSION ? "active" : ""}`}
            aria-selected={activeTab === CLUSTER_DASH_TAB_EXPANSION}
            aria-controls={panelExpansionId}
            tabIndex={activeTab === CLUSTER_DASH_TAB_EXPANSION ? 0 : -1}
            onClick={() => setActiveTab(CLUSTER_DASH_TAB_EXPANSION)}
          >
            Expansion factors
          </button>
        </li>
      </ul>
      <div
        id={panelExpansionId}
        role="tabpanel"
        aria-labelledby={tabExpansionId}
        className="tab-pane border border-top-0 rounded-bottom bg-white p-3 p-md-4"
      >
        {activeTab === CLUSTER_DASH_TAB_EXPANSION ? (
          <SectionExpansionFactor bundle={bundle} />
        ) : null}
      </div>
    </div>
  );
}

export default function PageClusterDashboard() {
  useDocumentTitle("Cluster dashboard — public");
  const { loading, bundle, error: loadError } = usePubDashboardBundle();
  const error = loadError || null;

  return (
    <div className="container py-4">
      <header className="mb-4">
        <h1 className="h3">Dashboard</h1>
      </header>

      {error ? <BannerErrorMessage message={error} /> : null}

      {loading ? (
        <LoadingMessage message="Loading cluster dashboard…" />
      ) : bundle == null ? null : bundle.status !== "ready" ? (
        <div className="alert alert-info" role="status">
          <div className="fw-semibold">Dashboard warming</div>
          <div className="small">
            {bundle.detail || "metrics_not_ready"} —{" "}
            {bundle.retry_hint || "check_back_later"}
          </div>
        </div>
      ) : (
        <ClusterDashboardTabs bundle={bundle} />
      )}
    </div>
  );
}
