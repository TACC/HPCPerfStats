import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { formatDecimalStandard } from "../utils/formatDecimal";
import {
  JOB_MONITOR_GPU_NO_DATA_ROW,
  jobMonitorSortComparable,
  patchJobMonitorGpuRowByUsername,
} from "../utils/job-monitor-gpu";
import { useDocumentTitle } from "../utils/useDocumentTitle";

function ariaSortValue(column, sortKey, sortDir) {
  if (sortKey !== column) return undefined;
  return sortDir === "asc" ? "ascending" : "descending";
}

function SortableTh({ column, sortKey, sortDir, onSort, children }) {
  return (
    <th scope="col" aria-sort={ariaSortValue(column, sortKey, sortDir)}>
      <button
        type="button"
        className="btn btn-link btn-sm p-0 text-start text-decoration-none job-monitor-sort-header text-dark"
        onClick={() => onSort(column)}
      >
        {children}
        {sortKey === column ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
      </button>
    </th>
  );
}

export default function JobMonitor() {
  useDocumentTitle("Job failure monitor");

  const [rows, setRows] = useState([]);
  const [windowDays, setWindowDays] = useState(30);
  const [inputDays, setInputDays] = useState("30");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sortKey, setSortKey] = useState("failed_rate");
  const [sortDir, setSortDir] = useState("desc"); // "asc" | "desc"

  const formatGpuValue = (value, loadingState) => {
    if (loadingState === "loading") return "Loading";
    if (value === null || value === undefined || value === "") return "N/A";
    const n = Number(value);
    if (!Number.isFinite(n)) return "N/A";
    return formatDecimalStandard(n);
  };

  const loadGpuRowsAsync = (rowsData, daysForWindow) => {
    (rowsData || []).forEach((row) => {
      const username = row?.username || "";
      if (!username) {
        setRows((prev) =>
          patchJobMonitorGpuRowByUsername(prev, username, JOB_MONITOR_GPU_NO_DATA_ROW),
        );
        return;
      }
      api
        .getJobMonitorGpuForUser(username, daysForWindow)
        .then((gpuRes) => {
          const hasData = !!gpuRes?.has_data;
          const patch = hasData
            ? {
                gpu_count_total: gpuRes.gpu_count_total,
                gpu_active_total: gpuRes.gpu_active_total,
                gpu_active_percentage: gpuRes.gpu_active_percentage,
                gpuLoadingState: "loaded",
              }
            : JOB_MONITOR_GPU_NO_DATA_ROW;
          setRows((prev) =>
            patchJobMonitorGpuRowByUsername(prev, username, patch),
          );
        })
        .catch(() => {
          setRows((prev) =>
            patchJobMonitorGpuRowByUsername(prev, username, JOB_MONITOR_GPU_NO_DATA_ROW),
          );
        });
    });
  };

  const loadData = (daysOverride) => {
    setLoading(true);
    setError(null);
    api
      .getJobMonitor(daysOverride)
      .then((res) => {
        const nextRows = (res.results || []).map((row) => ({
          ...row,
          gpu_count_total: null,
          gpu_active_total: null,
          gpu_active_percentage: null,
          gpuLoadingState: "loading",
        }));
        setRows(nextRows);
        if (typeof res.window_days === "number") {
          setWindowDays(res.window_days);
          setInputDays(String(res.window_days));
          loadGpuRowsAsync(nextRows, res.window_days);
        } else {
          loadGpuRowsAsync(nextRows, daysOverride);
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
    const av = jobMonitorSortComparable(a, sortKey);
    const bv = jobMonitorSortComparable(b, sortKey);
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
      <h1 className="h3">Job failure monitor</h1>
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
        <BannerErrorMessage
          variant="inline"
          className="text-danger mb-3"
          message={`Error loading job monitor data: ${error}`}
        />
      )}
      {!loading && !error && (
        <div className="table-responsive">
          <table className="table table-sm table-bordered">
            <caption className="visually-hidden">
              Job outcomes by user for the last {windowDays} days
            </caption>
            <thead>
              <tr>
                <SortableTh
                  column="username"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  User
                </SortableTh>
                <SortableTh
                  column="total_jobs"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  Number of jobs
                </SortableTh>
                <SortableTh
                  column="failed_jobs"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  Number of failed jobs
                </SortableTh>
                <SortableTh
                  column="failed_rate"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  % failed
                </SortableTh>
                <SortableTh
                  column="timedout_jobs"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  Number of timed out jobs
                </SortableTh>
                <SortableTh
                  column="timedout_rate"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  % timed out
                </SortableTh>
                <SortableTh
                  column="gpu_count_total"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  Total GPUs Allocated
                </SortableTh>
                <SortableTh
                  column="gpu_active_total"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  Number of GPUs Active
                </SortableTh>
                <SortableTh
                  column="gpu_active_percentage"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                >
                  Percentage of GPUs Active
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => (
                <tr key={row.username || "(unknown)"}>
                  <td>
                    {row.username ? (
                      <Link to={`/username/${encodeURIComponent(row.username)}/`}>
                        {row.username}
                      </Link>
                    ) : (
                      "(unknown)"
                    )}
                  </td>
                  <td>{formatDecimalStandard(row.total_jobs)}</td>
                  <td>{formatDecimalStandard(row.failed_jobs)}</td>
                  <td>{formatDecimalStandard(row.failed_rate)}</td>
                  <td>{formatDecimalStandard(row.timedout_jobs)}</td>
                  <td>{formatDecimalStandard(row.timedout_rate)}</td>
                  <td>{formatGpuValue(row.gpu_count_total, row.gpuLoadingState)}</td>
                  <td>{formatGpuValue(row.gpu_active_total, row.gpuLoadingState)}</td>
                  <td>
                    {row.gpuLoadingState === "loading"
                      ? "Loading"
                      : row.gpu_active_percentage === null ||
                    row.gpu_active_percentage === undefined ||
                    row.gpu_active_percentage === ""
                      ? "N/A"
                      : `${formatDecimalStandard(row.gpu_active_percentage)}%`}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan="9" className="text-center text-muted">
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

