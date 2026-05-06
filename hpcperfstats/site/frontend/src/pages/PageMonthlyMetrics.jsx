import { useEffect, useState } from "react";
import { fetchPubMonthlyMetrics } from "../api.js";
import { useDocumentTitle } from "../utils/useDocumentTitle.js";
import { formatDecimalStandard } from "../utils/formatDecimal.js";

function SectionExpansionFactor({ bundle }) {
  const efSection = bundle?.sections?.expansion_factor || {};
  const monthly = efSection.monthly_daily_histograms || {};
  const yearly = efSection.yearly_weekly_histograms || {};

  const renderHist = (title, payloadMap, axisHint) => {
    const keys = Object.keys(payloadMap).sort();
    if (!keys.length) {
      return (
        <p className="text-muted mb-0">
          No histogram data for this grouping yet.
        </p>
      );
    }
    return (
      <div className="mb-4">
        <h3 className="h6">{title}</h3>
        <p className="small text-muted">{axisHint}</p>
        {keys.map((k) => {
          const block = payloadMap[k];
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
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <section aria-labelledby="expansion-factor-heading">
      <h2 id="expansion-factor-heading" className="h5">
        Expansion factor
      </h2>
      <p className="text-muted">
        Scheduler-centric expansion factor aggregates precomputed offline from accounting timestamps.
        Monthly views summarize distributions of{" "}
        <strong>daily mean EF</strong> values; yearly views summarize distributions of{" "}
        <strong>weekly mean EF</strong> values (ISO weeks).
      </p>
      {renderHist(
        "Monthly — histogram of daily mean EF",
        monthly,
        "Each chart lists completed-job calendar months.",
      )}
      {renderHist(
        "Yearly — histogram of weekly mean EF",
        yearly,
        "Each chart lists calendar years; buckets are ISO weeks inside that year.",
      )}
    </section>
  );
}

export default function PageMonthlyMetrics() {
  useDocumentTitle("Monthly metrics — public");
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchPubMonthlyMetrics()
      .then((data) => {
        if (!cancelled) setBundle(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="container py-4">
      <header className="mb-4">
        <h1 className="h3">Monthly metrics</h1>
        <p className="text-muted mb-0">
          Public cluster dashboards built from pre-warmed aggregates (no live heavy queries).
        </p>
      </header>

      {error ? (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      ) : null}

      {!bundle ? (
        <p className="text-muted">Loading…</p>
      ) : bundle.status !== "ready" ? (
        <div className="alert alert-info" role="status">
          <div className="fw-semibold">Dashboard warming</div>
          <div className="small">
            {bundle.detail || "metrics_not_ready"} —{" "}
            {bundle.retry_hint || "check_back_later"}
          </div>
        </div>
      ) : (
        <SectionExpansionFactor bundle={bundle} />
      )}
    </div>
  );
}
