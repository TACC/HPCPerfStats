import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";
import { aggregateLiveJobsByHost } from "../utils/aggregateLiveJobsByHost";
import { formatMinsAgo } from "../utils/formatRelativeTime";

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

/** Grey cell when a host is known from host_data (admin monitor) but has no live job row. */
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

/**
 * Known hosts from GET /api/admin_monitor/?section=hosts (host_data, last 8d),
 * merged with live /live/jobs/ aggregation. Idle hosts (no live row) are grey.
 * @param {Array<{host?: string, last_time?: string, age_bucket?: string}>} adminHostStats
 * @param {Array<{host: string, usage: number, maxCpu: number, maxMem: number, updatedTs: number, jids: string[]}>} liveByHost
 */
function mergeAdminHostsWithLive(adminHostStats, liveByHost) {
  const adminRows = (adminHostStats || []).filter(
    (h) =>
      h &&
      typeof h.host === "string" &&
      h.host.includes(".") &&
      !h._debug_74ebbb,
  );
  const liveMap = new Map(
    liveByHost.map((e) => [e.host, { ...e, isLive: true }]),
  );
  const consumedLiveFqdns = new Set();
  const out = [];

  function liveEntryForAdminFqdn(adminFqdn) {
    const exact = liveMap.get(adminFqdn);
    if (exact) {
      return exact;
    }
    const short = shortHostname(adminFqdn);
    const candidates = liveByHost.filter(
      (e) => shortHostname(e.host) === short,
    );
    if (candidates.length === 1) {
      return { ...candidates[0], isLive: true };
    }
    return null;
  }

  const sortedAdmin = [...adminRows].sort((a, b) =>
    shortHostname(a.host).localeCompare(shortHostname(b.host)),
  );
  for (const a of sortedAdmin) {
    const live = liveEntryForAdminFqdn(a.host);
    if (live) {
      consumedLiveFqdns.add(live.host);
      out.push({ ...live, adminMeta: a });
    } else {
      out.push({
        host: a.host,
        usage: 0,
        maxCpu: 0,
        maxMem: 0,
        updatedTs: 0,
        jids: [],
        isLive: false,
        adminMeta: a,
      });
    }
  }

  const extras = liveByHost
    .filter((e) => !consumedLiveFqdns.has(e.host))
    .map((e) => ({ ...e, isLive: true }));
  extras.sort((a, b) => b.maxCpu - a.maxCpu || b.usage - a.usage);
  out.push(...extras);

  return out;
}

export default function LiveNodeHeatmap() {
  const [rows, setRows] = useState([]);
  const [adminHostStats, setAdminHostStats] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [, bumpMins] = useState(0);

  const load = () => {
    setError(null);
    Promise.all([
      api.getLiveJobs(),
      api.getAdminMonitorSection("hosts").catch(() => ({ host_stats: [] })),
    ])
      .then(([liveRes, adminRes]) => {
        setRows(liveRes.results || []);
        setAdminHostStats(adminRes.host_stats || []);
      })
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

  const liveByHost = useMemo(() => aggregateLiveJobsByHost(rows), [rows]);
  const displayEntries = useMemo(
    () => mergeAdminHostsWithLive(adminHostStats, liveByHost),
    [adminHostStats, liveByHost],
  );

  if (loading && rows.length === 0 && adminHostStats.length === 0) {
    return <LoadingMessage message="Loading node heatmap…" />;
  }

  return (
    <>
      <h3>Live node usage heatmap</h3>
      <p className="text-muted">
        Nodes with recent daemon snapshots (same source as{" "}
        <Link to="/live_jobs">Live jobs</Link>). Color reflects{" "}
        <strong>CPU utilization</strong> (0–100%): purple is idle, red is fully
        loaded. Each job uses one node and 
        cells are grey until live telemetry arrives.
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
      </div>
      {displayEntries.length === 0 ? (
        <p className="text-muted">No live node data yet.</p>
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
