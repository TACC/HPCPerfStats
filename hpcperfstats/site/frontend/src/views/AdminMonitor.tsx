import { useEffect, useMemo, useState } from "react";
import type {
  AdminMonitorHostRow,
  AdminMonitorSectionResponse,
  AdminMonitorXaltStats,
  FreshnessBucket,
} from "@/types/view-models";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Table } from "@/components/ui/table";
import { cn } from "@/lib/utils";
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
  ok: { label: "OK (≤ 10 minutes)", class: "badge-freshness-ok" },
  gt_10min: { label: "> 10 minutes", class: "badge-freshness-gt_10min" },
  gt_hour: { label: "> 1 hour", class: "badge-freshness-gt_hour" },
  gt_day: { label: "> 1 day", class: "badge-freshness-gt_day" },
  gt_week: { label: "> 1 week", class: "badge-freshness-gt_week" },
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
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">HPCPerfStats Monitor</h1>

      <div className="admin-monitor-section">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="admin-monitor-section-header h-auto w-full justify-start font-semibold"
          onClick={() => setHostTimeExpanded((e) => !e)}
          aria-expanded={hostTimeExpanded}
          aria-controls="admin-monitor-host-time"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {hostTimeExpanded ? "▼" : "▶"}
          </span>
          {`Most recent host data timestamps in database${hostHeaderSummary}`}
        </Button>
        <div
          id="admin-monitor-host-time"
          className="admin-monitor-section-body"
          hidden={!hostTimeExpanded}
          role="region"
          aria-label="Most recent host data timestamps in database"
        >
          <div className="admin-monitor-action-row">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="admin-monitor-refresh-button"
              onClick={() => loadHostStats(true)}
              disabled={hostLoading}
            >
              Refresh Data
            </Button>
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
              <div className="mb-2 flex flex-wrap items-center">
                <p className="mb-1 mr-3">
                  Status buckets:{" "}
                  <Badge className="badge-freshness-ok">OK (≤ 10 minutes)</Badge>{" "}
                  <Badge className="badge-freshness-gt_10min">{"> 10 minutes"}</Badge>{" "}
                  <Badge className="badge-freshness-gt_hour">{"> 1 hour"}</Badge>{" "}
                  <Badge className="badge-freshness-gt_day">{"> 1 day"}</Badge>{" "}
                  <Badge className="badge-freshness-gt_week">{"> 1 week"}</Badge>
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="ms-auto"
                  disabled={!nonRespondingHosts36}
                  onClick={handleCopyNonResponding36}
                >
                  Non Responding Hosts - 36 Hours
                </Button>
              </div>
              <Table className="border text-sm">
                <caption className="sr-only">
                  Monitor agents reporting host data. Sort by host, last timestamp, or
                  status freshness using the column header buttons.
                </caption>
                <thead>
                  <tr>
                    <th scope="col" aria-sort={tableSortAriaSort("host", hostSort.column, hostSort.direction)}>
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        className="h-auto p-0 font-inherit"
                        onClick={() => handleHostSort("host")}
                      >
                        Host{" "}
                        {tableSortColumnArrow("host", hostSort.column, hostSort.direction, {
                          leadingSpace: false,
                        })}
                      </Button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort(
                        "last_time",
                        hostSort.column,
                        hostSort.direction,
                      )}
                    >
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        className="h-auto p-0 font-inherit"
                        onClick={() => handleHostSort("last_time")}
                      >
                        Last Timestamp{" "}
                        {tableSortColumnArrow(
                          "last_time",
                          hostSort.column,
                          hostSort.direction,
                          { leadingSpace: false },
                        )}
                      </Button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort("status", hostSort.column, hostSort.direction)}
                    >
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        className="h-auto p-0 font-inherit"
                        onClick={() => handleHostSort("status")}
                      >
                        Status{" "}
                        {tableSortColumnArrow("status", hostSort.column, hostSort.direction, {
                          leadingSpace: false,
                        })}
                      </Button>
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
                          <Badge className={badge.class}>{badge.label}</Badge>
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
                </Table>
            </>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="admin-monitor-section-header h-auto w-full justify-start font-semibold"
          onClick={() => setXaltExpanded((e) => !e)}
          aria-expanded={xaltExpanded}
          aria-controls="admin-monitor-xalt-coverage"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {xaltExpanded ? "▼" : "▶"}
          </span>
          {`XALT job coverage (last 3 days)${xaltHeaderSummary}`}
        </Button>
        <div
          id="admin-monitor-xalt-coverage"
          className="admin-monitor-section-body"
          hidden={!xaltExpanded}
          role="region"
          aria-label="XALT job coverage (last 3 days)"
        >
          <div className="admin-monitor-action-row">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="admin-monitor-refresh-button"
              onClick={() => loadXaltStats(true)}
              disabled={xaltLoading}
            >
              Refresh Data
            </Button>
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
                  className="mb-2 text-destructive"
                  message={xaltStats.error}
                />
              )}
              {!xaltStats.error && (
                <>
                  <div className="mb-2 text-muted-foreground">
                    Total JIDs: {String(xaltStats.total_jids ?? "—")} · Found with
                    XALT: {String(xaltStats.jids_with_xalt_data ?? "—")} · Missing:{" "}
                    {String(xaltStats.jids_missing_xalt_data ?? "—")}
                  </div>

                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <Label className="mb-0 mr-2 font-normal" htmlFor="xaltListMode">
                      Show list:
                    </Label>
                    <select
                      id="xaltListMode"
                      className="h-7 rounded-lg border border-input bg-transparent px-2.5 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
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
                    <Table className="border text-sm">
                        <caption className="sr-only">
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
                              <td className="text-muted-foreground text-center">
                                No missing JIDs listed.
                              </td>
                            </tr>
                          )}
                          {xaltStats.missing_jids_truncated && (
                            <tr>
                              <td className="text-muted-foreground">
                                Showing first{" "}
                                {String(
                                  xaltStats.missing_jids_limit ?? "—"
                                )}{" "}
                                missing JIDs.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </Table>
                  )}

                  {xaltListMode === "found" && (xaltStats.jids_with_xalt_data ?? 0) > 0 && (
                    <Table className="border text-sm">
                        <caption className="sr-only">
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
                              <td className="text-muted-foreground text-center">
                                No found JIDs listed.
                              </td>
                            </tr>
                          )}
                          {xaltStats.found_jids_truncated && (
                            <tr>
                              <td className="text-muted-foreground">
                                Showing first{" "}
                                {String(xaltStats.found_jids_limit ?? "—")} found JIDs.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </Table>
                  )}

                  {xaltListMode === "missing" &&
                    xaltStats.jids_missing_xalt_data === 0 && (
                      <div className="text-green-600">
                        All JIDs in the last 3 days have corresponding XALT data.
                      </div>
                    )}

                  {xaltListMode === "found" &&
                    xaltStats.jids_with_xalt_data === 0 && (
                      <div className="text-destructive">
                        No JIDs in the last 3 days have corresponding XALT data.
                      </div>
                    )}
                </>
              )}
            </>
          )}
          {!xaltLoading && !xaltError && !xaltStats && (
            <div className="text-muted-foreground">No XALT coverage statistics available.</div>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="admin-monitor-section-header h-auto w-full justify-start font-semibold"
          onClick={() => setRabbitHostTimeExpanded((e) => !e)}
          aria-expanded={rabbitHostTimeExpanded}
          aria-controls="admin-monitor-rabbit-host-time"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {rabbitHostTimeExpanded ? "▼" : "▶"}
          </span>
          {`Most recent host data timestamps in RabbitMQ${rabbitHostHeaderSummary}`}
        </Button>
        <div
          id="admin-monitor-rabbit-host-time"
          className="admin-monitor-section-body"
          hidden={!rabbitHostTimeExpanded}
          role="region"
          aria-label="Most recent host data timestamps in RabbitMQ"
        >
          <div className="admin-monitor-action-row">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="admin-monitor-refresh-button"
              onClick={() => loadRabbitHostStats(true)}
              disabled={rabbitHostLoading}
            >
              Refresh Data
            </Button>
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
            <Table className="border text-sm">
                <caption className="sr-only">
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
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        className="h-auto p-0 font-inherit"
                        onClick={() => handleRabbitHostSort("host")}
                      >
                        Host{" "}
                        {tableSortColumnArrow(
                          "host",
                          rabbitHostSort.column,
                          rabbitHostSort.direction,
                          { leadingSpace: false },
                        )}
                      </Button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort(
                        "last_time",
                        rabbitHostSort.column,
                        rabbitHostSort.direction,
                      )}
                    >
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        className="h-auto p-0 font-inherit"
                        onClick={() => handleRabbitHostSort("last_time")}
                      >
                        Last Timestamp{" "}
                        {tableSortColumnArrow(
                          "last_time",
                          rabbitHostSort.column,
                          rabbitHostSort.direction,
                          { leadingSpace: false },
                        )}
                      </Button>
                    </th>
                    <th
                      scope="col"
                      aria-sort={tableSortAriaSort(
                        "status",
                        rabbitHostSort.column,
                        rabbitHostSort.direction,
                      )}
                    >
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        className="h-auto p-0 font-inherit"
                        onClick={() => handleRabbitHostSort("status")}
                      >
                        Status{" "}
                        {tableSortColumnArrow(
                          "status",
                          rabbitHostSort.column,
                          rabbitHostSort.direction,
                          { leadingSpace: false },
                        )}
                      </Button>
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
                          <Badge className={badge.class}>{badge.label}</Badge>
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
              </Table>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="admin-monitor-section-header h-auto w-full justify-start font-semibold"
          onClick={() => setTimescaledbExpanded((e) => !e)}
          aria-expanded={timescaledbExpanded}
          aria-controls="admin-monitor-timescaledb-stats"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {timescaledbExpanded ? "▼" : "▶"}
          </span>
          TimescaleDB statistics
        </Button>
        <div
          id="admin-monitor-timescaledb-stats"
          className="admin-monitor-section-body"
          hidden={!timescaledbExpanded}
          role="region"
          aria-label="TimescaleDB statistics"
        >
          <div className="admin-monitor-action-row">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="admin-monitor-refresh-button"
              onClick={() => loadTimescaledbStats(true)}
              disabled={timescaledbLoading}
            >
              Refresh Data
            </Button>
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
            <Table className="border text-sm">
              <caption className="sr-only">
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
                    <td colSpan={2} className="text-muted-foreground">
                      No TimescaleDB statistics available.
                    </td>
                  </tr>
                )}
              </tbody>
              </Table>
          )}
          {!timescaledbLoading && !timescaledbError && !timescaledbStats && (
            <div className="text-muted-foreground">No TimescaleDB statistics available.</div>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="admin-monitor-section-header h-auto w-full justify-start font-semibold"
          onClick={() => setCacheExpanded((e) => !e)}
          aria-expanded={cacheExpanded}
          aria-controls="admin-monitor-cache-stats"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {cacheExpanded ? "▼" : "▶"}
          </span>
          Cache / Redis statistics
        </Button>
        <div
          id="admin-monitor-cache-stats"
          className="admin-monitor-section-body"
          hidden={!cacheExpanded}
          role="region"
          aria-label="Cache and Redis statistics"
        >
          <div className="admin-monitor-action-row">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="admin-monitor-refresh-button"
              onClick={() => loadCacheStats(true)}
              disabled={cacheLoading}
            >
              Refresh Data
            </Button>
          </div>
          {cacheLoading && <LoadingMessage message="Loading cache statistics…" />}
          {cacheError && !cacheLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading cache stats: ${cacheError}`}
            />
          )}
          {!cacheLoading && !cacheError && cacheStats && Object.keys(cacheStats).length > 0 && (
            <Table className="border text-sm">
              <caption className="sr-only">
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
              </Table>
          )}
          {!cacheLoading && !cacheError && (!cacheStats || Object.keys(cacheStats).length === 0) && (
            <div className="text-muted-foreground">No cache statistics available.</div>
          )}
        </div>
      </div>

      <div className="admin-monitor-section">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="admin-monitor-section-header h-auto w-full justify-start font-semibold"
          onClick={() => setRabbitExpanded((e) => !e)}
          aria-expanded={rabbitExpanded}
          aria-controls="admin-monitor-rabbitmq-stats"
        >
          <span className="admin-monitor-section-chevron" aria-hidden>
            {rabbitExpanded ? "▼" : "▶"}
          </span>
          RabbitMQ statistics
        </Button>
        <div
          id="admin-monitor-rabbitmq-stats"
          className="admin-monitor-section-body"
          hidden={!rabbitExpanded}
          role="region"
          aria-label="RabbitMQ statistics"
        >
          <div className="admin-monitor-action-row">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="admin-monitor-refresh-button"
              onClick={() => loadRabbitStats(true)}
              disabled={rabbitLoading}
            >
              Refresh Data
            </Button>
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
                  className="mb-2 text-destructive"
                  message={`RabbitMQ reported an error: ${rabbitStats.error}`}
                />
              )}
              <Table className="border text-sm">
                <caption className="sr-only">
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
                      <td colSpan={2} className="text-muted-foreground">
                        No RabbitMQ statistics available.
                      </td>
                    </tr>
                  )}
                </tbody>
                </Table>
            </>
          )}
          {!rabbitLoading &&
            !rabbitError &&
            !rabbitStats && (
              <div className="text-muted-foreground">No RabbitMQ statistics available.</div>
            )}
        </div>
      </div>
    </>
  );
}
