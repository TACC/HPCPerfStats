import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";

function formatMinsAgo(epochSec) {
  if (epochSec == null || !Number.isFinite(Number(epochSec))) {
    return "—";
  }
  const secs = Math.floor(Date.now() / 1000) - Number(epochSec);
  const mins = Math.max(0, Math.floor(secs / 60));
  if (mins === 1) {
    return "1 min ago";
  }
  return `${mins} mins ago`;
}

export default function LiveJobs() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [, bumpMins] = useState(0);

  const load = () => {
    setError(null);
    api
      .getLiveJobs()
      .then((res) => setRows(res.results || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => bumpMins((n) => n + 1), 60_000);
    return () => window.clearInterval(id);
  }, []);

  if (loading && rows.length === 0) {
    return <LoadingMessage message="Loading live jobs…" />;
  }

  return (
    <>
      <h3>Live jobs</h3>
      <p className="text-muted">
        Recent CPU and memory utilization from daemon snapshots (Redis). Rows
        expire when updates stop (~15 minutes).
      </p>
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      <div className="table-responsive">
        <table className="table table-sm table-striped">
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Host</th>
              <th>CPU %</th>
              <th>Mem %</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-muted">
                  No live rows yet.
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={`${r.jid}-${r.host}-${r.updated_ts}`}>
                  <td>
                    <Link to={`/job/${encodeURIComponent(r.jid)}`}>{r.jid}</Link>
                  </td>
                  <td>{r.host}</td>
                  <td>{r.cpu_util != null ? r.cpu_util.toFixed(2) : "—"}</td>
                  <td>{r.mem_util != null ? r.mem_util.toFixed(2) : "—"}</td>
                  <td>{formatMinsAgo(r.updated_ts)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
