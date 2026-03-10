import { useEffect, useState } from "react";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";

const BADGE_MAP = {
  ok: { label: "OK (≤ 10 minutes)", class: "badge badge-freshness-ok" },
  gt_10min: { label: "> 10 minutes", class: "badge badge-freshness-gt_10min" },
  gt_hour: { label: "> 1 hour", class: "badge badge-freshness-gt_hour" },
  gt_day: { label: "> 1 day", class: "badge badge-freshness-gt_day" },
  gt_week: { label: "> 1 week", class: "badge badge-freshness-gt_week" },
};

const ROW_CLASS = {
  ok: "tr-freshness-ok",
  gt_10min: "tr-freshness-gt_10min",
  gt_hour: "tr-freshness-gt_hour",
  gt_day: "tr-freshness-gt_day",
  gt_week: "tr-freshness-gt_week",
};

export default function AdminMonitor() {
  const [hostTimeExpanded, setHostTimeExpanded] = useState(false);
  const [cacheExpanded, setCacheExpanded] = useState(false);
  const [hostStats, setHostStats] = useState([]);
  const [hostLoading, setHostLoading] = useState(false);
  const [hostError, setHostError] = useState(null);
  const [hostRequested, setHostRequested] = useState(false);
  const [cacheStats, setCacheStats] = useState(null);
  const [cacheLoading, setCacheLoading] = useState(false);
  const [cacheError, setCacheError] = useState(null);
  const [cacheRequested, setCacheRequested] = useState(false);

  // Lazily load host stats when the section is first expanded.
  useEffect(() => {
    if (!hostTimeExpanded || hostRequested) return;
    setHostRequested(true);
    setHostLoading(true);
    setHostError(null);
    api
      .getAdminMonitorSection("hosts")
      .then((res) => {
        setHostStats(res.host_stats || []);
      })
      .catch((e) => setHostError(e.message))
      .finally(() => setHostLoading(false));
  }, [hostTimeExpanded, hostRequested]);

  // Lazily load cache stats when the section is first expanded.
  useEffect(() => {
    if (!cacheExpanded || cacheRequested) return;
    setCacheRequested(true);
    setCacheLoading(true);
    setCacheError(null);
    api
      .getAdminMonitorSection("cache")
      .then((res) => {
        setCacheStats(res.cache_stats || null);
      })
      .catch((e) => setCacheError(e.message))
      .finally(() => setCacheLoading(false));
  }, [cacheExpanded, cacheRequested]);

  const totalHosts = hostStats.length;
  const bucketCounts = hostStats.reduce(
    (acc, row) => {
      const b = row.age_bucket || "gt_week";
      acc[b] = (acc[b] || 0) + 1;
      return acc;
    },
    {}
  );

  const hostHeaderSummary =
    !hostLoading && !hostError && hostStats.length > 0
      ? ` - Total hosts: ${totalHosts} · ${Object.keys(BADGE_MAP)
          .map((key) => `${BADGE_MAP[key].label}: ${bucketCounts[key] ?? 0}`)
          .join(" · ")}`
      : "";

  return (
    <>
      <h3>Admin Monitor</h3>

      <div className="admin-monitor-section">
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm admin-monitor-section-header"
          onClick={() => setHostTimeExpanded((e) => !e)}
          aria-expanded={hostTimeExpanded}
          aria-controls="admin-monitor-host-time"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {hostTimeExpanded ? "▼" : "▶"}
          </span>
          {`Host last seen timestamps${hostHeaderSummary}`}
        </button>
        <div
          id="admin-monitor-host-time"
          className="admin-monitor-section-body"
          hidden={!hostTimeExpanded}
          role="region"
          aria-label="Host last seen timestamps"
        >
          {hostLoading && <LoadingMessage message="Loading host timestamps…" />}
          {hostError && !hostLoading && (
            <div className="text-danger">Error loading host data: {hostError}</div>
          )}
          {!hostLoading && !hostError && (
            <>
          <p>
            Status buckets:{" "}
            <span className="badge badge-freshness-ok">OK (≤ 10 minutes)</span>{" "}
            <span className="badge badge-freshness-gt_10min">{"> 10 minutes"}</span>{" "}
            <span className="badge badge-freshness-gt_hour">{"> 1 hour"}</span>{" "}
            <span className="badge badge-freshness-gt_day">{"> 1 day"}</span>{" "}
            <span className="badge badge-freshness-gt_week">{"> 1 week"}</span>
          </p>
              <table className="table table-sm table-bordered">
                <thead>
                  <tr>
                    <th>Host</th>
                    <th>Last Timestamp</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {hostStats.map((row, i) => {
                    const badge = BADGE_MAP[row.age_bucket] || BADGE_MAP.gt_week;
                    const rowClass = ROW_CLASS[row.age_bucket] || "";
                    return (
                      <tr key={row.host + i} className={rowClass}>
                        <td>{row.host}</td>
                        <td>{row.last_time || "—"}</td>
                        <td>
                          <span className={`badge ${badge.class}`}>{badge.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                  {hostStats.length === 0 && (
                    <tr>
                      <td colSpan="3" className="text-center">
                        No host data available.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm admin-monitor-section-header"
          onClick={() => setCacheExpanded((e) => !e)}
          aria-expanded={cacheExpanded}
          aria-controls="admin-monitor-cache-stats"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {cacheExpanded ? "▼" : "▶"}
          </span>
          Cache / Redis statistics
        </button>
        <div
          id="admin-monitor-cache-stats"
          className="admin-monitor-section-body"
          hidden={!cacheExpanded}
          role="region"
          aria-label="Cache and Redis statistics"
        >
          {cacheLoading && <LoadingMessage message="Loading cache statistics…" />}
          {cacheError && !cacheLoading && (
            <div className="text-danger">Error loading cache stats: {cacheError}</div>
          )}
          {!cacheLoading && !cacheError && cacheStats && Object.keys(cacheStats).length > 0 && (
            <table className="table table-sm table-bordered">
              <tbody>
                {Object.entries(cacheStats).map(([key, value]) => (
                  <tr key={key}>
                    <th scope="row">{key}</th>
                    <td>{value === null || value === undefined ? "—" : String(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!cacheLoading && !cacheError && (!cacheStats || Object.keys(cacheStats).length === 0) && (
            <div className="text-muted">No cache statistics available.</div>
          )}
        </div>
      </div>
    </>
  );
}
