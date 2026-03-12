import { useEffect, useState } from "react";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";

export default function JobMonitor() {
  const [rows, setRows] = useState([]);
  const [windowDays, setWindowDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .getJobMonitor()
      .then((res) => {
        setRows(res.results || []);
        if (typeof res.window_days === "number") {
          setWindowDays(res.window_days);
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <h3>Job Monitor</h3>
      <p className="text-muted">
        Aggregated job outcomes by user for the last {windowDays} days.
      </p>
      {loading && <LoadingMessage message="Loading job monitor data…" />}
      {error && !loading && (
        <div className="text-danger mb-3">Error loading job monitor data: {error}</div>
      )}
      {!loading && !error && (
        <div className="table-responsive">
          <table className="table table-sm table-bordered">
            <thead>
              <tr>
                <th>User</th>
                <th>Number of jobs</th>
                <th>Number of failed jobs</th>
                <th>% failed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.username || "(unknown)"}>
                  <td>{row.username || "(unknown)"}</td>
                  <td>{row.total_jobs}</td>
                  <td>{row.failed_jobs}</td>
                  <td>{row.failed_rate.toFixed ? row.failed_rate.toFixed(2) : row.failed_rate}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan="4" className="text-center text-muted">
                    No jobs found in the selected time window.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

