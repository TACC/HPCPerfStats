import { useEffect, useMemo, useState } from "react";
import { api } from "@/api";
import type {
  AdminMonitorHostRow,
  AdminMonitorSectionResponse,
  AdminMonitorXaltStats,
  FreshnessBucket,
} from "@/types/view-models";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { useTableSort, type TableSortState } from "../hooks/useTableSort";
import { createAdminMonitorSectionLoader } from "../utils/create-admin-monitor-section-loader";
import { copyToClipboard } from "../utils/copy-to-clipboard";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { tableSortAriaSort, tableSortColumnArrow } from "../utils/table-sort-a11y";
import { useDocumentTitle } from "../utils/useDocumentTitle";

function formatAdminMonitorNumericStatistic(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" && Number.isFinite(value)) {
    return formatDecimalStandard(value);
  }
  return String(value);
}

const BADGE_MAP: Record<
  FreshnessBucket,
  { label: string; class: string }
> = {
  ok: { label: "OK (≤ 10 minutes)", class: "badge badge-freshness-ok" },
  gt_10min: { label: "> 10 minutes", class: "badge badge-freshness-gt_10min" },
  gt_hour: { label: "> 1 hour", class: "badge badge-freshness-gt_hour" },
  gt_day: { label: "> 1 day", class: "badge badge-freshness-gt_day" },
  gt_week: { label: "> 1 week", class: "badge badge-freshness-gt_week" },
};

const ROW_CLASS: Record<FreshnessBucket, string> = {
  ok: "tr-freshness-ok",
  gt_10min: "tr-freshness-gt_10min",
  gt_hour: "tr-freshness-gt_hour",
  gt_day: "tr-freshness-gt_day",
  gt_week: "tr-freshness-gt_week",
};

const HOST_STATUS_ORDER: Record<FreshnessBucket, number> = {
  ok: 0,
  gt_10min: 1,
  gt_hour: 2,
  gt_day: 3,
  gt_week: 4,
};

function compareAdminMonitorHostRows(
  a: AdminMonitorHostRow,
  b: AdminMonitorHostRow,
  sort: TableSortState,
) {
  const dir = sort.direction === "asc" ? 1 : -1;
  if (sort.column === "host") {
    return a.host.localeCompare(b.host) * dir;
  }
  if (sort.column === "last_time") {
    const aTime = a.last_time ? new Date(a.last_time).getTime() : 0;
    const bTime = b.last_time ? new Date(b.last_time).getTime() : 0;
    return (aTime - bTime) * dir;
  }
  if (sort.column === "status") {
    const aBucket =
      HOST_STATUS_ORDER[a.age_bucket as FreshnessBucket] ?? HOST_STATUS_ORDER.gt_week;
    const bBucket =
      HOST_STATUS_ORDER[b.age_bucket as FreshnessBucket] ?? HOST_STATUS_ORDER.gt_week;
    return (aBucket - bBucket) * dir;
  }
  return 0;
}

export default function AdminMonitor() {
  useDocumentTitle("HPCPerfStats Monitor");

  const [hostTimeExpanded, setHostTimeExpanded] = useState(false);
  const [rabbitHostTimeExpanded, setRabbitHostTimeExpanded] = useState(false);
  const [cacheExpanded, setCacheExpanded] = useState(false);
  const [rabbitExpanded, setRabbitExpanded] = useState(false);
  const [timescaledbExpanded, setTimescaledbExpanded] = useState(false);
  const [hostStats, setHostStats] = useState<AdminMonitorHostRow[]>([]);
  const { sort: hostSort, onSort: handleHostSort } = useTableSort("host", "asc", "asc");
  const [hostLoading, setHostLoading] = useState(false);
  const [hostError, setHostError] = useState<string | null>(null);
  const [hostRequested, setHostRequested] = useState(false);
  const [rabbitHostStats, setRabbitHostStats] = useState<AdminMonitorHostRow[]>([]);
  const {
    sort: rabbitHostSort,
    onSort: handleRabbitHostSort,
  } = useTableSort("host", "asc", "asc");
  const [rabbitHostLoading, setRabbitHostLoading] = useState(false);
  const [rabbitHostError, setRabbitHostError] = useState<string | null>(null);
  const [rabbitHostRequested, setRabbitHostRequested] = useState(false);
  const [cacheStats, setCacheStats] = useState<Record<string, unknown> | null>(null);
  const [cacheLoading, setCacheLoading] = useState(false);
  const [cacheError, setCacheError] = useState<string | null>(null);
  const [cacheRequested, setCacheRequested] = useState(false);
  const [rabbitStats, setRabbitStats] = useState<Record<string, unknown> | null>(null);
  const [rabbitLoading, setRabbitLoading] = useState(false);
  const [rabbitError, setRabbitError] = useState<string | null>(null);
  const [rabbitRequested, setRabbitRequested] = useState(false);
  const [timescaledbStats, setTimescaledbStats] = useState<Record<string, unknown> | null>(null);
  const [timescaledbLoading, setTimescaledbLoading] = useState(false);
  const [timescaledbError, setTimescaledbError] = useState<string | null>(null);
  const [timescaledbRequested, setTimescaledbRequested] = useState(false);
  const [xaltExpanded, setXaltExpanded] = useState(false);
  const [xaltStats, setXaltStats] = useState<AdminMonitorXaltStats | null>(null);
  const [xaltLoading, setXaltLoading] = useState(false);
  const [xaltError, setXaltError] = useState<string | null>(null);
  const [xaltRequested, setXaltRequested] = useState(false);
  const [xaltListMode, setXaltListMode] = useState("missing");
  const [nonRespondingHosts36, setNonRespondingHosts36] = useState("");

  // Only show fully qualified hostnames (contain a dot) in the UI.
  const fqdnHostStats = hostStats.filter(
    (row) => row.host && row.host.includes(".")
  );
  const fqdnRabbitHostStats = rabbitHostStats.filter(
    (row) => row.host && row.host.includes(".")
  );

  const loadHostStats = useMemo(
    () =>
      createAdminMonitorSectionLoader({
        section: "hosts",
        pickResponse: (res: AdminMonitorSectionResponse) =>
          (res.host_stats as AdminMonitorHostRow[] | undefined) || [],
        setLoading: setHostLoading,
        setError: setHostError,
        setData: setHostStats,
        apiClient: api,
      }),
    [],
  );

  const loadRabbitHostStats = useMemo(
    () =>
      createAdminMonitorSectionLoader({
        section: "rabbitmq_hosts",
        pickResponse: (res: AdminMonitorSectionResponse) =>
          (res.rabbitmq_host_stats as AdminMonitorHostRow[] | undefined) || [],
        setLoading: setRabbitHostLoading,
        setError: setRabbitHostError,
        setData: setRabbitHostStats,
        apiClient: api,
      }),
    [],
  );

  const loadCacheStats = useMemo(
    () =>
      createAdminMonitorSectionLoader({
        section: "cache",
        pickResponse: (res: AdminMonitorSectionResponse) =>
          (res.cache_stats as Record<string, unknown> | null | undefined) || null,
        setLoading: setCacheLoading,
        setError: setCacheError,
        setData: setCacheStats,
        apiClient: api,
      }),
    [],
  );

  const loadRabbitStats = useMemo(
    () =>
      createAdminMonitorSectionLoader({
        section: "rabbitmq",
        pickResponse: (res: AdminMonitorSectionResponse) =>
          (res.rabbitmq_stats as Record<string, unknown> | null | undefined) || null,
        setLoading: setRabbitLoading,
        setError: setRabbitError,
        setData: setRabbitStats,
        apiClient: api,
      }),
    [],
  );

  const loadTimescaledbStats = useMemo(
    () =>
      createAdminMonitorSectionLoader({
        section: "timescaledb",
        pickResponse: (res: AdminMonitorSectionResponse) =>
          (res.timescaledb_stats as Record<string, unknown> | null | undefined) || null,
        setLoading: setTimescaledbLoading,
        setError: setTimescaledbError,
        setData: setTimescaledbStats,
        apiClient: api,
      }),
    [],
  );

  const loadXaltStats = useMemo(
    () =>
      createAdminMonitorSectionLoader({
        section: "xalt",
        pickResponse: (res: AdminMonitorSectionResponse) =>
          (res.xalt_stats as AdminMonitorXaltStats | null | undefined) || null,
        setLoading: setXaltLoading,
        setError: setXaltError,
        setData: setXaltStats,
        apiClient: api,
      }),
    [],
  );

  // Lazily load section payloads when each block is first expanded.
  useEffect(() => {
    if (hostTimeExpanded && !hostRequested) {
      setHostRequested(true);
      loadHostStats();
    }
    if (rabbitHostTimeExpanded && !rabbitHostRequested) {
      setRabbitHostRequested(true);
      loadRabbitHostStats();
    }
    if (cacheExpanded && !cacheRequested) {
      setCacheRequested(true);
      loadCacheStats();
    }
    if (rabbitExpanded && !rabbitRequested) {
      setRabbitRequested(true);
      loadRabbitStats();
    }
    if (timescaledbExpanded && !timescaledbRequested) {
      setTimescaledbRequested(true);
      loadTimescaledbStats();
    }
    if (xaltExpanded && !xaltRequested) {
      setXaltRequested(true);
      loadXaltStats();
    }
  }, [
    hostTimeExpanded,
    hostRequested,
    loadHostStats,
    rabbitHostTimeExpanded,
    rabbitHostRequested,
    loadRabbitHostStats,
    cacheExpanded,
    cacheRequested,
    loadCacheStats,
    rabbitExpanded,
    rabbitRequested,
    loadRabbitStats,
    timescaledbExpanded,
    timescaledbRequested,
    loadTimescaledbStats,
    xaltExpanded,
    xaltRequested,
    loadXaltStats,
  ]);

  // Build comma-separated list of FQDNs not seen in the past 36 hours when the
  // host section is open and hostStats are available.
  useEffect(() => {
    if (!hostTimeExpanded || hostLoading || hostError) return;
    const cutoffMs = Date.now() - 36 * 60 * 60 * 1000;
    const fqdnSet = new Set();
    for (const row of fqdnHostStats) {
      const host = row.host || "";
      const ts = row.last_time;
      if (!host || !ts) continue;
      const t = new Date(ts).getTime();
      if (!Number.isFinite(t) || t >= cutoffMs) continue;
      fqdnSet.add(host);
    }
    const list = Array.from(fqdnSet).sort().join(",");
    setNonRespondingHosts36(list);
  }, [hostTimeExpanded, hostLoading, hostError, fqdnHostStats]);

  const totalHosts = fqdnHostStats.length;
  const bucketCounts = fqdnHostStats.reduce<Record<string, number>>(
    (acc, row) => {
      const b = String(row.age_bucket || "gt_week");
      acc[b] = (acc[b] || 0) + 1;
      return acc;
    },
    {},
  );

  const hostHeaderSummary =
    !hostLoading && !hostError && fqdnHostStats.length > 0
      ? ` - Total hosts: ${totalHosts} · ${(Object.keys(BADGE_MAP) as FreshnessBucket[])
          .map((key) => `${BADGE_MAP[key].label}: ${bucketCounts[key] ?? 0}`)
          .join(" · ")}`
      : "";
  const rabbitHostTotal = fqdnRabbitHostStats.length;
  const rabbitHostBucketCounts = fqdnRabbitHostStats.reduce<Record<string, number>>(
    (acc, row) => {
      const b = String(row.age_bucket || "gt_week");
      acc[b] = (acc[b] || 0) + 1;
      return acc;
    },
    {},
  );
  const rabbitHostHeaderSummary =
    !rabbitHostLoading && !rabbitHostError && fqdnRabbitHostStats.length > 0
      ? ` - Total hosts: ${rabbitHostTotal} · ${(Object.keys(BADGE_MAP) as FreshnessBucket[])
          .map((key) => `${BADGE_MAP[key].label}: ${rabbitHostBucketCounts[key] ?? 0}`)
          .join(" · ")}`
      : "";

  const xaltHeaderSummary =
    !xaltLoading &&
    !xaltError &&
    xaltStats &&
    xaltStats.total_jids !== undefined
      ? ` - Total JIDs: ${xaltStats.total_jids} · Found: ${
          xaltStats.jids_with_xalt_data ?? 0
        } · Missing: ${xaltStats.jids_missing_xalt_data ?? 0}`
      : "";

  const sortedHostStats = useMemo(
    () =>
      [...fqdnHostStats].sort((a, b) =>
        compareAdminMonitorHostRows(a, b, hostSort),
      ),
    [fqdnHostStats, hostSort],
  );
  const sortedRabbitHostStats = useMemo(
    () =>
      [...fqdnRabbitHostStats].sort((a, b) =>
        compareAdminMonitorHostRows(a, b, rabbitHostSort),
      ),
    [fqdnRabbitHostStats, rabbitHostSort],
  );

  const formatHostTime = (value: string | null | undefined) => {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString();
  };

  const handleCopyNonResponding36 = async () => {
    if (!nonRespondingHosts36) return;
    const ok = await copyToClipboard(nonRespondingHosts36);
    if (!ok) console.error("Failed to copy non-responding hosts list");
  };

  return (
    <>
      <h1 className="h2 mb-3">HPCPerfStats Monitor</h1>

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
          {`Most recent host data timestamps in database${hostHeaderSummary}`}
        </button>
        <div
          id="admin-monitor-host-time"
          className="admin-monitor-section-body"
          hidden={!hostTimeExpanded}
          role="region"
          aria-label="Most recent host data timestamps in database"
        >
          <div className="admin-monitor-action-row">
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm admin-monitor-refresh-button"
              onClick={() => loadHostStats(true)}
              disabled={hostLoading}
            >
              Refresh Data
            </button>
          </div>
          {hostLoading && <LoadingMessage message="Loading host timestamps…" />}
          {hostError && !hostLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading host data: ${hostError}`}
            />
          )}
          {!hostLoading && !hostError && (
            <>
              <div className="d-flex flex-wrap align-items-center mb-2">
                <p className="mb-1 me-3">
                  Status buckets:{" "}
                  <span className="badge badge-freshness-ok">OK (≤ 10 minutes)</span>{" "}
                  <span className="badge badge-freshness-gt_10min">{"> 10 minutes"}</span>{" "}
                  <span className="badge badge-freshness-gt_hour">{"> 1 hour"}</span>{" "}
                  <span className="badge badge-freshness-gt_day">{"> 1 day"}</span>{" "}
                  <span className="badge badge-freshness-gt_week">{"> 1 week"}</span>
                </p>
                <button
                  type="button"
                  className="btn btn-outline-secondary btn-sm ms-auto"
                  disabled={!nonRespondingHosts36}
                  onClick={handleCopyNonResponding36}
                >
                  Non Responding Hosts - 36 Hours
                </button>
              </div>
              <div className="table-responsive">
                <table className="table table-sm table-bordered">
                <caption className="visually-hidden">
                  Monitor agents reporting host data. Sort by host, last timestamp, or
                  status freshness using the column header buttons.
                </caption>
                <thead>
                  <tr>
                    <th scope="col" aria-sort={tableSortAriaSort("host", hostSort.column, hostSort.direction)}>
                      <button
                        type="button"
                        className="btn btn-link btn-sm p-0"
                        onClick={() => handleHostSort("host")}
                      >
                        Host{" "}
                        {tableSortColumnArrow("host", hostSort.column, hostSort.direction, {
                          leadingSpace: false,
                        })}
                      </button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort(
                        "last_time",
                        hostSort.column,
                        hostSort.direction,
                      )}
                    >
                      <button
                        type="button"
                        className="btn btn-link btn-sm p-0"
                        onClick={() => handleHostSort("last_time")}
                      >
                        Last Timestamp{" "}
                        {tableSortColumnArrow(
                          "last_time",
                          hostSort.column,
                          hostSort.direction,
                          { leadingSpace: false },
                        )}
                      </button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort("status", hostSort.column, hostSort.direction)}
                    >
                      <button
                        type="button"
                        className="btn btn-link btn-sm p-0"
                        onClick={() => handleHostSort("status")}
                      >
                        Status{" "}
                        {tableSortColumnArrow("status", hostSort.column, hostSort.direction, {
                          leadingSpace: false,
                        })}
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedHostStats.map((row, i) => {
                    const badge =
                      BADGE_MAP[(row.age_bucket as FreshnessBucket) || "gt_week"] ||
                      BADGE_MAP.gt_week;
                    const rowClass =
                      ROW_CLASS[(row.age_bucket as FreshnessBucket) || "gt_week"] || "";
                    return (
                      <tr key={row.host + i} className={rowClass}>
                        <td>{row.host}</td>
                        <td>{formatHostTime(row.last_time)}</td>
                        <td>
                          <span className={`badge ${badge.class}`}>{badge.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                  {fqdnHostStats.length === 0 && (
                    <tr>
                      <td colSpan={3} className="text-center">
                        No host data available.
                      </td>
                    </tr>
                  )}
                </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm admin-monitor-section-header"
          onClick={() => setXaltExpanded((e) => !e)}
          aria-expanded={xaltExpanded}
          aria-controls="admin-monitor-xalt-coverage"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {xaltExpanded ? "▼" : "▶"}
          </span>
          {`XALT job coverage (last 3 days)${xaltHeaderSummary}`}
        </button>
        <div
          id="admin-monitor-xalt-coverage"
          className="admin-monitor-section-body"
          hidden={!xaltExpanded}
          role="region"
          aria-label="XALT job coverage (last 3 days)"
        >
          <div className="admin-monitor-action-row">
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm admin-monitor-refresh-button"
              onClick={() => loadXaltStats(true)}
              disabled={xaltLoading}
            >
              Refresh Data
            </button>
          </div>
          {xaltLoading && <LoadingMessage message="Loading XALT coverage…" />}
          {xaltError && !xaltLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading XALT coverage: ${xaltError}`}
            />
          )}
          {!xaltLoading && !xaltError && xaltStats && (
            <>
              {xaltStats.error && (
                <BannerErrorMessage
                  variant="inline"
                  className="text-danger mb-2"
                  message={xaltStats.error}
                />
              )}
              {!xaltStats.error && (
                <>
                  <div className="mb-2 text-muted">
                    Total JIDs: {String(xaltStats.total_jids ?? "—")} · Found with
                    XALT: {String(xaltStats.jids_with_xalt_data ?? "—")} · Missing:{" "}
                    {String(xaltStats.jids_missing_xalt_data ?? "—")}
                  </div>

                  <div className="d-flex flex-wrap align-items-center mb-2 gap-2">
                    <label className="form-label mb-0 me-2" htmlFor="xaltListMode">
                      Show list:
                    </label>
                    <select
                      id="xaltListMode"
                      className="form-select form-select-sm"
                      value={xaltListMode}
                      onChange={(e) => setXaltListMode(e.target.value)}
                    >
                      <option value="missing">
                        Missing JIDs ({String(xaltStats.jids_missing_xalt_data ?? 0)})
                      </option>
                      <option value="found">
                        Found JIDs ({String(xaltStats.jids_with_xalt_data ?? 0)})
                      </option>
                    </select>
                  </div>

                  {xaltListMode === "missing" && (xaltStats.jids_missing_xalt_data ?? 0) > 0 && (
                    <div className="table-responsive">
                      <table className="table table-sm table-bordered">
                        <caption className="visually-hidden">
                          Job IDs from the last three days that are missing XALT coverage.
                          List may be truncated.
                        </caption>
                        <thead>
                          <tr>
                            <th scope="col">Missing JIDs (truncated)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Array.isArray(xaltStats.missing_jids) &&
                            xaltStats.missing_jids.length > 0 &&
                            xaltStats.missing_jids.map((jid, i) => (
                              <tr key={`${jid}-${i}`}>
                                <td>{jid}</td>
                              </tr>
                            ))}
                          {(!Array.isArray(xaltStats.missing_jids) ||
                            xaltStats.missing_jids.length === 0) && (
                            <tr>
                              <td className="text-muted text-center">
                                No missing JIDs listed.
                              </td>
                            </tr>
                          )}
                          {xaltStats.missing_jids_truncated && (
                            <tr>
                              <td className="text-muted">
                                Showing first{" "}
                                {String(
                                  xaltStats.missing_jids_limit ?? "—"
                                )}{" "}
                                missing JIDs.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {xaltListMode === "found" && (xaltStats.jids_with_xalt_data ?? 0) > 0 && (
                    <div className="table-responsive">
                      <table className="table table-sm table-bordered">
                        <caption className="visually-hidden">
                          Job IDs from the last three days that have XALT coverage. List may
                          be truncated.
                        </caption>
                        <thead>
                          <tr>
                            <th scope="col">Found JIDs (truncated)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Array.isArray(xaltStats.found_jids) &&
                            xaltStats.found_jids.length > 0 &&
                            xaltStats.found_jids.map((jid, i) => (
                              <tr key={`${jid}-${i}`}>
                                <td>{jid}</td>
                              </tr>
                            ))}
                          {(!Array.isArray(xaltStats.found_jids) ||
                            xaltStats.found_jids.length === 0) && (
                            <tr>
                              <td className="text-muted text-center">
                                No found JIDs listed.
                              </td>
                            </tr>
                          )}
                          {xaltStats.found_jids_truncated && (
                            <tr>
                              <td className="text-muted">
                                Showing first{" "}
                                {String(xaltStats.found_jids_limit ?? "—")} found JIDs.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {xaltListMode === "missing" &&
                    xaltStats.jids_missing_xalt_data === 0 && (
                      <div className="text-success">
                        All JIDs in the last 3 days have corresponding XALT data.
                      </div>
                    )}

                  {xaltListMode === "found" &&
                    xaltStats.jids_with_xalt_data === 0 && (
                      <div className="text-danger">
                        No JIDs in the last 3 days have corresponding XALT data.
                      </div>
                    )}
                </>
              )}
            </>
          )}
          {!xaltLoading && !xaltError && !xaltStats && (
            <div className="text-muted">No XALT coverage statistics available.</div>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm admin-monitor-section-header"
          onClick={() => setRabbitHostTimeExpanded((e) => !e)}
          aria-expanded={rabbitHostTimeExpanded}
          aria-controls="admin-monitor-rabbit-host-time"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {rabbitHostTimeExpanded ? "▼" : "▶"}
          </span>
          {`Most recent host data timestamps in RabbitMQ${rabbitHostHeaderSummary}`}
        </button>
        <div
          id="admin-monitor-rabbit-host-time"
          className="admin-monitor-section-body"
          hidden={!rabbitHostTimeExpanded}
          role="region"
          aria-label="Most recent host data timestamps in RabbitMQ"
        >
          <div className="admin-monitor-action-row">
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm admin-monitor-refresh-button"
              onClick={() => loadRabbitHostStats(true)}
              disabled={rabbitHostLoading}
            >
              Refresh Data
            </button>
          </div>
          {rabbitHostLoading && (
            <LoadingMessage message="Loading RabbitMQ host timestamps…" />
          )}
          {rabbitHostError && !rabbitHostLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading RabbitMQ host data: ${rabbitHostError}`}
            />
          )}
          {!rabbitHostLoading && !rabbitHostError && (
            <div className="table-responsive">
              <table className="table table-sm table-bordered">
                <caption className="visually-hidden">
                  Hosts seen via RabbitMQ and their last data timestamps. Sort by host, last
                  timestamp, or status freshness using the column header buttons.
                </caption>
                <thead>
                  <tr>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort(
                        "host",
                        rabbitHostSort.column,
                        rabbitHostSort.direction,
                      )}
                    >
                      <button
                        type="button"
                        className="btn btn-link btn-sm p-0"
                        onClick={() => handleRabbitHostSort("host")}
                      >
                        Host{" "}
                        {tableSortColumnArrow(
                          "host",
                          rabbitHostSort.column,
                          rabbitHostSort.direction,
                          { leadingSpace: false },
                        )}
                      </button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort(
                        "last_time",
                        rabbitHostSort.column,
                        rabbitHostSort.direction,
                      )}
                    >
                      <button
                        type="button"
                        className="btn btn-link btn-sm p-0"
                        onClick={() => handleRabbitHostSort("last_time")}
                      >
                        Last Timestamp{" "}
                        {tableSortColumnArrow(
                          "last_time",
                          rabbitHostSort.column,
                          rabbitHostSort.direction,
                          { leadingSpace: false },
                        )}
                      </button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort(
                        "status",
                        rabbitHostSort.column,
                        rabbitHostSort.direction,
                      )}
                    >
                      <button
                        type="button"
                        className="btn btn-link btn-sm p-0"
                        onClick={() => handleRabbitHostSort("status")}
                      >
                        Status{" "}
                        {tableSortColumnArrow(
                          "status",
                          rabbitHostSort.column,
                          rabbitHostSort.direction,
                          { leadingSpace: false },
                        )}
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedRabbitHostStats.map((row, i) => {
                    const badge =
                      BADGE_MAP[(row.age_bucket as FreshnessBucket) || "gt_week"] ||
                      BADGE_MAP.gt_week;
                    const rowClass =
                      ROW_CLASS[(row.age_bucket as FreshnessBucket) || "gt_week"] || "";
                    return (
                      <tr key={row.host + i} className={rowClass}>
                        <td>{row.host}</td>
                        <td>{formatHostTime(row.last_time)}</td>
                        <td>
                          <span className={`badge ${badge.class}`}>{badge.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                  {fqdnRabbitHostStats.length === 0 && (
                    <tr>
                      <td colSpan={3} className="text-center">
                        No RabbitMQ host data available.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm admin-monitor-section-header"
          onClick={() => setTimescaledbExpanded((e) => !e)}
          aria-expanded={timescaledbExpanded}
          aria-controls="admin-monitor-timescaledb-stats"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {timescaledbExpanded ? "▼" : "▶"}
          </span>
          TimescaleDB statistics
        </button>
        <div
          id="admin-monitor-timescaledb-stats"
          className="admin-monitor-section-body"
          hidden={!timescaledbExpanded}
          role="region"
          aria-label="TimescaleDB statistics"
        >
          <div className="admin-monitor-action-row">
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm admin-monitor-refresh-button"
              onClick={() => loadTimescaledbStats(true)}
              disabled={timescaledbLoading}
            >
              Refresh Data
            </button>
          </div>
          {timescaledbLoading && (
            <LoadingMessage message="Loading TimescaleDB statistics…" />
          )}
          {timescaledbError && !timescaledbLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading TimescaleDB stats: ${timescaledbError}`}
            />
          )}
          {!timescaledbLoading && !timescaledbError && timescaledbStats && (
            <div className="table-responsive">
              <table className="table table-sm table-bordered">
              <caption className="visually-hidden">
                TimescaleDB database and hypertable size statistics.
              </caption>
              <tbody>
                {(() => {
                  const LABELS = {
                    database_name: "Database name",
                    server_version: "PostgreSQL server version",
                    timescaledb_version: "TimescaleDB extension version",
                    hypertable_count: "Number of hypertables",
                    chunk_count: "Total chunks",
                    compressed_chunk_count: "Compressed chunks",
                    compressed_chunks_size_pretty: "Compressed chunk data size",
                    uncompressed_chunks_size_pretty: "Uncompressed chunk data size",
                    pending_compression_size_pretty:
                      "Approx. data pending compression",
                    host_data_row_estimate: "host_data row estimate",
                    host_data_size_bytes: "host_data total size (bytes)",
                    host_data_size_pretty: "host_data total size",
                  };
                  return Object.entries(LABELS)
                    .filter(
                      ([key]) =>
                        timescaledbStats[key] !== null &&
                        timescaledbStats[key] !== undefined
                    )
                    .map(([key, label]) => (
                      <tr key={key}>
                        <th scope="row">{label}</th>
                        <td>
                          {typeof timescaledbStats[key] === "number"
                            ? formatAdminMonitorNumericStatistic(timescaledbStats[key])
                            : String(timescaledbStats[key])}
                        </td>
                      </tr>
                    ));
                })()}
                {(!timescaledbStats ||
                  Object.entries(timescaledbStats).filter(
                    ([, value]) => value !== null && value !== undefined
                  ).length === 0) && (
                  <tr>
                    <td colSpan={2} className="text-muted">
                      No TimescaleDB statistics available.
                    </td>
                  </tr>
                )}
              </tbody>
              </table>
            </div>
          )}
          {!timescaledbLoading && !timescaledbError && !timescaledbStats && (
            <div className="text-muted">No TimescaleDB statistics available.</div>
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
          <div className="admin-monitor-action-row">
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm admin-monitor-refresh-button"
              onClick={() => loadCacheStats(true)}
              disabled={cacheLoading}
            >
              Refresh Data
            </button>
          </div>
          {cacheLoading && <LoadingMessage message="Loading cache statistics…" />}
          {cacheError && !cacheLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading cache stats: ${cacheError}`}
            />
          )}
          {!cacheLoading && !cacheError && cacheStats && Object.keys(cacheStats).length > 0 && (
            <div className="table-responsive">
              <table className="table table-sm table-bordered">
              <caption className="visually-hidden">
                Cache and Redis key statistics for the application.
              </caption>
              <tbody>
                {Object.entries(cacheStats).map(([key, value]) => {
                  let displayValue;
                  if (key === "most_used_cached_keys" && Array.isArray(value)) {
                    displayValue = value
                      .map((entry) => entry && entry.key)
                      .filter(Boolean)
                      .join(", ");
                  } else {
                    displayValue = formatAdminMonitorNumericStatistic(value);
                  }
                  return (
                    <tr key={key}>
                      <th scope="row">{key}</th>
                      <td>{displayValue}</td>
                    </tr>
                  );
                })}
              </tbody>
              </table>
            </div>
          )}
          {!cacheLoading && !cacheError && (!cacheStats || Object.keys(cacheStats).length === 0) && (
            <div className="text-muted">No cache statistics available.</div>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm admin-monitor-section-header"
          onClick={() => setRabbitExpanded((e) => !e)}
          aria-expanded={rabbitExpanded}
          aria-controls="admin-monitor-rabbitmq-stats"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {rabbitExpanded ? "▼" : "▶"}
          </span>
          RabbitMQ statistics
        </button>
        <div
          id="admin-monitor-rabbitmq-stats"
          className="admin-monitor-section-body"
          hidden={!rabbitExpanded}
          role="region"
          aria-label="RabbitMQ statistics"
        >
          <div className="admin-monitor-action-row">
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm admin-monitor-refresh-button"
              onClick={() => loadRabbitStats(true)}
              disabled={rabbitLoading}
            >
              Refresh Data
            </button>
          </div>
          {rabbitLoading && (
            <LoadingMessage message="Loading RabbitMQ statistics…" />
          )}
          {rabbitError && !rabbitLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading RabbitMQ stats: ${rabbitError}`}
            />
          )}
          {!rabbitLoading && !rabbitError && rabbitStats && (
            <>
              {rabbitStats.error && (
                <BannerErrorMessage
                  variant="inline"
                  className="text-danger mb-2"
                  message={`RabbitMQ reported an error: ${rabbitStats.error}`}
                />
              )}
              <div className="table-responsive">
                <table className="table table-sm table-bordered">
                <caption className="visually-hidden">
                  RabbitMQ queue depth, consumer, and message volume statistics.
                </caption>
                <tbody>
                  {(() => {
                    const LABELS: Record<string, string> = {
                      queue: "Queue",
                      messages: "Total messages (ready + unacked)",
                      messages_ready: "Messages ready",
                      messages_unacknowledged: "Messages unacknowledged",
                      consumers: "Consumers",
                      message_bytes: "Total bytes (all messages)",
                      message_bytes_ready: "Bytes for ready messages",
                      message_bytes_unacknowledged:
                        "Bytes for unacknowledged messages",
                      messages_published_total:
                        "Messages published (total since broker start)",
                      messages_delivered_total:
                        "Messages delivered/consumed (total since broker start)",
                      messages_published_since_snapshot:
                        "Messages published since previous snapshot",
                      snapshot_hours: "Hours covered by previous snapshot window",
                      messages_published_last_24h_estimate:
                        "Approx. messages published in last 24 hours",
                    };
                    return Object.entries(rabbitStats)
                      .filter(([key, value]) => key in LABELS && value !== null && value !== undefined)
                      .map(([key, value]) => (
                        <tr key={key}>
                          <th scope="row">{LABELS[key]}</th>
                          <td>{formatAdminMonitorNumericStatistic(value)}</td>
                        </tr>
                      ));
                  })()}
                  {(!rabbitStats ||
                    Object.entries(rabbitStats).filter(
                      ([key, value]) => value !== null && value !== undefined
                    ).length === 0) && (
                    <tr>
                      <td colSpan={2} className="text-muted">
                        No RabbitMQ statistics available.
                      </td>
                    </tr>
                  )}
                </tbody>
                </table>
              </div>
            </>
          )}
          {!rabbitLoading &&
            !rabbitError &&
            !rabbitStats && (
              <div className="text-muted">No RabbitMQ statistics available.</div>
            )}
        </div>
      </div>
    </>
  );
}
