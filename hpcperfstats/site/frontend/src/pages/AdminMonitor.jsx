import { useEffect, useState } from "react";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";

const BADGE_MAP = {
  ok: { label: "OK (≤ 10 minutes)", class: "badge-secondary" },
  gt_10min: { label: "> 10 minutes", class: "badge-info" },
  gt_hour: { label: "> 1 hour", class: "badge-warning" },
  gt_day: { label: "> 1 day", class: "badge-danger" },
  gt_week: { label: "> 1 week", class: "badge-dark" },
};

const ROW_CLASS = {
  gt_10min: "table-info",
  gt_hour: "table-warning",
  gt_day: "table-danger",
  gt_week: "table-dark",
};

export default function AdminMonitor() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [hostTimeExpanded, setHostTimeExpanded] = useState(true);

  useEffect(() => {
    api
      .getAdminMonitor()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingMessage message="Loading admin monitor…" />;
  if (error) return <div className="container text-danger">Error: {error}</div>;
  if (!data) return null;

  const { host_stats = [] } = data;

  return (
    <>
      <h3>Admin Monitor</h3>

      <div className="admin-monitor-section">
        <button
          type="button"
          className="admin-monitor-section-header"
          onClick={() => setHostTimeExpanded((e) => !e)}
          aria-expanded={hostTimeExpanded}
          aria-controls="admin-monitor-host-time"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {hostTimeExpanded ? "▼" : "▶"}
          </span>
          Host last seen timestamps
        </button>
        <div
          id="admin-monitor-host-time"
          className="admin-monitor-section-body"
          hidden={!hostTimeExpanded}
          role="region"
          aria-label="Host last seen timestamps"
        >
          <p>
            Status buckets:{" "}
            <span className="badge badge-secondary">OK (≤ 10 minutes)</span>{" "}
            <span className="badge badge-info">{"> 10 minutes"}</span>{" "}
            <span className="badge badge-warning">{"> 1 hour"}</span>{" "}
            <span className="badge badge-danger">{"> 1 day"}</span>{" "}
            <span className="badge badge-dark">{"> 1 week"}</span>
          </p>
          <table className="table table-condensed table-bordered">
            <thead>
              <tr>
                <th>Host</th>
                <th>Last Timestamp</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {host_stats.map((row, i) => {
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
              {host_stats.length === 0 && (
                <tr>
                  <td colSpan="3" className="text-center">
                    No host data available.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
