import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";
import { aggregateLiveJobsByHost } from "../utils/aggregateLiveJobsByHost";
import { fetchHeatmapKnownHosts } from "../utils/fetchHeatmapKnownHosts";
import { formatMinsAgo } from "../utils/formatRelativeTime";
import { mergeHeatmapHostsWithLive } from "../utils/mergeHeatmapHostsWithLive";

/** Map CPU utilisation 0–100% to a purple (low) → red (high) hue ramp. */
function cpuToHeatColor(cpuPct) {
  const t = Math.max(0, Math.min(100, Number(cpuPct) || 0)) / 100;
  const hue = 280 * (1 - t);
  return `hsl(${hue}, 72%, 42%)`;
}

function shortHostname(fqdn) {
  if (!fqdn || typeof fqdn !== "string") {
    return "";
  }
  const i = fqdn.indexOf(".");
  return i === -1 ? fqdn : fqdn.slice(0, i);
}

/** Grey cell when a host is known from host_data but has no live job row. */
const IDLE_HEATMAP_CELL_BG = "hsl(220, 6%, 36%)";

function buildCellTitle(entry) {
  if (!entry.isLive) {
    const last = entry.adminMeta?.last_time || "—";
    return [entry.host, "No live job telemetry", `Last host_data: ${last}`].join(
      "\n",
    );
  }
  const jobs =
    entry.jids.length <= 3
      ? entry.jids.join(", ")
      : `${entry.jids.slice(0, 3).join(", ")} (+${entry.jids.length - 3})`;
  return [
    entry.host,
    `CPU%: ${entry.maxCpu.toFixed(1)}%`,
    `Mem%: ${entry.maxMem.toFixed(1)}%`,
    `Job: ${jobs || "—"}`,
    `Updated: ${formatMinsAgo(entry.updatedTs)}`,
  ].join("\n");
}

export default function LiveNodeHeatmap() {
  const [rows, setRows] = useState([]);
  const [knownHostStats, setKnownHostStats] = useState([]);
  const [hostsReady, setHostsReady] = useState(false);
  const [error, setError] = useState(null);
  const [, bumpMins] = useState(0);

  /** On mount: load all known hosts first so the grid appears immediately (grey if idle). */
  useEffect(() => {
    let cancelled = false;
    fetchHeatmapKnownHosts({ refresh: true })
      .then((stats) => {
        if (!cancelled) {
          setKnownHostStats(stats);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError((prev) => prev || e.message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setHostsReady(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadLiveJobs = () => {
    api
      .getLiveJobs()
      .then((res) => setRows(res.results || []))
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    loadLiveJobs();
    const t = setInterval(loadLiveJobs, 15000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => bumpMins((n) => n + 1), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const liveByHost = useMemo(() => aggregateLiveJobsByHost(rows), [rows]);
  const displayEntries = useMemo(
    () => mergeHeatmapHostsWithLive(knownHostStats, liveByHost),
    [knownHostStats, liveByHost],
  );

  if (!hostsReady) {
    return <LoadingMessage message="Loading node heatmap…" />;
  }

  return (
    <>
      <h3>Live node usage heatmap</h3>
      <p className="text-muted">
        All hosts seen in <code>host_data</code> over the last 8 days appear on
        load (grey when no live job). Color reflects{" "}
        <strong>CPU utilization</strong> (0–100%) from{" "}
        <Link to="/live_jobs">Live jobs</Link> when a node is in use.
      </p>
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      <div className="mb-3 d-flex flex-wrap align-items-center gap-3">
        <span className="text-muted small">CPU %</span>
        {[0, 25, 50, 75, 100].map((u) => (
          <div key={u} className="d-flex align-items-center gap-1">
            <span
              className="rounded border"
              style={{
                width: 28,
                height: 18,
                backgroundColor: cpuToHeatColor(u),
              }}
              aria-hidden
            />
            <span className="small">{u}%</span>
          </div>
        ))}
        <span
          className="rounded border ms-2"
          style={{
            width: 28,
            height: 18,
            backgroundColor: IDLE_HEATMAP_CELL_BG,
          }}
          aria-hidden
        />
        <span className="small text-muted">No live job</span>
      </div>
      {displayEntries.length === 0 ? (
        <p className="text-muted">
          No hosts in host_data for the last 8 days yet.
        </p>
      ) : (
        <div
          className="live-node-heatmap-grid"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(5.5rem, 1fr))",
            gap: "6px",
          }}
        >
          {displayEntries.map((entry) => (
            <div
              key={entry.host}
              className="rounded border text-center small py-2 px-1 text-white text-truncate"
              style={{
                backgroundColor: entry.isLive
                  ? cpuToHeatColor(entry.maxCpu)
                  : IDLE_HEATMAP_CELL_BG,
                textShadow: "0 0 2px rgba(0,0,0,0.75)",
                minHeight: "3.5rem",
                cursor: "default",
              }}
              title={buildCellTitle(entry)}
            >
              <div className="fw-semibold text-truncate" title={entry.host}>
                {shortHostname(entry.host)}
              </div>
              <div className="opacity-90 fw-semibold">
                {entry.isLive ? `${entry.maxCpu.toFixed(1)}%` : "—"}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
