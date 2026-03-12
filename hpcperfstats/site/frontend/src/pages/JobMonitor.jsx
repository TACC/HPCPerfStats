import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";

export default function JobMonitor() {
  const [rows, setRows] = useState([]);
  const [windowDays, setWindowDays] = useState(30);
  const [inputDays, setInputDays] = useState("30");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sortKey, setSortKey] = useState("failed_rate");
  const [sortDir, setSortDir] = useState("desc"); // "asc" | "desc"

  const loadData = (daysOverride) => {
    setLoading(true);
    setError(null);
    api
      .getJobMonitor(daysOverride)
      .then((res) => {
        setRows(res.results || []);
        if (typeof res.window_days === "number") {
          setWindowDays(res.window_days);
          setInputDays(String(res.window_days));
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSort = (key) => {
    setSortKey((currentKey) => {
      if (currentKey === key) {
        setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
        return currentKey;
      }
      setSortDir("desc");
      return key;
    });
  };

  const sortedRows = [...rows].sort((a, b) => {
    const getVal = (row, key) => {
      if (key === "username") {
        return (row.username || "").toLowerCase();
      }
      return Number(row[key] ?? 0);
    };
    const av = getVal(a, sortKey);
    const bv = getVal(b, sortKey);
    if (av < bv) return sortDir === "asc" ? -1 : 1;
    if (av > bv) return sortDir === "asc" ? 1 : -1;
    // Tiebreaker by username
    const au = (a.username || "").toLowerCase();
    const bu = (b.username || "").toLowerCase();
    if (au < bu) return -1;
    if (au > bu) return 1;
    return 0;
  });

  return (
    <>
      <h3>Job Monitor</h3>
      <p className="text-muted">
        Aggregated job outcomes by user for the last {windowDays} days. Only users
        who have run more than {windowDays / 2} jobs in this window are included.
      </p>
      <form
        className="row g-2 align-items-center mb-3"
        onSubmit={(e) => {
          e.preventDefault();
          const n = parseInt(inputDays, 10);
          if (!Number.isFinite(n)) {
            setError("Days must be a number between 1 and 365.");
            return;
          }
          if (n < 1 || n > 365) {
            setError("Days must be between 1 and 365.");
            return;
          }
          setError(null);
          loadData(n);
        }}
      >
        <div className="col-auto">
          <label htmlFor="job-monitor-days" className="col-form-label">
            Window (days):
          </label>
        </div>
        <div className="col-auto">
          <input
            id="job-monitor-days"
            type="number"
            min="1"
            max="365"
            className="form-control form-control-sm"
            value={inputDays}
            onChange={(e) => setInputDays(e.target.value)}
          />
        </div>
        <div className="col-auto">
          <button type="submit" className="btn btn-outline-secondary btn-sm">
            Apply
          </button>
        </div>
      </form>
      {loading && <LoadingMessage message="Loading job monitor data…" />}
      {error && !loading && (
        <div className="text-danger mb-3">Error loading job monitor data: {error}</div>
      )}
      {!loading && !error && (
        <div className="table-responsive">
          <table className="table table-sm table-bordered">
            <thead>
              <tr>
                <th
                  role="button"
                  onClick={() => handleSort("username")}
                >
                  User
                  {sortKey === "username" && (sortDir === "asc" ? " ▲" : " ▼")}
                </th>
                <th
                  role="button"
                  onClick={() => handleSort("total_jobs")}
                >
                  Number of jobs
                  {sortKey === "total_jobs" && (sortDir === "asc" ? " ▲" : " ▼")}
                </th>
                <th
                  role="button"
                  onClick={() => handleSort("failed_jobs")}
                >
                  Number of failed jobs
                  {sortKey === "failed_jobs" && (sortDir === "asc" ? " ▲" : " ▼")}
                </th>
                <th
                  role="button"
                  onClick={() => handleSort("failed_rate")}
                >
                  % failed
                  {sortKey === "failed_rate" && (sortDir === "asc" ? " ▲" : " ▼")}
                </th>
                <th
                  role="button"
                  onClick={() => handleSort("timedout_jobs")}
                >
                  Number of timed out jobs
                  {sortKey === "timedout_jobs" && (sortDir === "asc" ? " ▲" : " ▼")}
                </th>
                <th
                  role="button"
                  onClick={() => handleSort("timedout_rate")}
                >
                  % timed out
                  {sortKey === "timedout_rate" && (sortDir === "asc" ? " ▲" : " ▼")}
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => (
                <tr key={row.username || "(unknown)"}>
                  <td>{row.username || "(unknown)"}</td>
                  <td>{row.total_jobs}</td>
                  <td>{row.failed_jobs}</td>
                  <td>
                    {row.failed_rate?.toFixed
                      ? row.failed_rate.toFixed(2)
                      : row.failed_rate}
                  </td>
                  <td>{row.timedout_jobs}</td>
                  <td>
                    {row.timedout_rate?.toFixed
                      ? row.timedout_rate.toFixed(2)
                      : row.timedout_rate}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan="6" className="text-center text-muted">
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

