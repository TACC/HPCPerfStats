"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useAdminMonitorSectionQuery } from "@/hooks/use-admin-monitor-section";
import { ChevronRight } from "lucide-react";
import type {
  AdminMonitorHostRow,
  AdminMonitorIngestCoverage,
  AdminMonitorIngestCoverageGroup,
  AdminMonitorSectionResponse,
  IngestCoveragePairClass,
  AdminMonitorTelemetryHealth,
  AdminMonitorXaltStats,
  FreshnessBucket,
} from "@/types/view-models";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { useTableSort, type TableSortState } from "../hooks/useTableSort";
import { copyToClipboard } from "../utils/copy-to-clipboard";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { tableSortAriaSort, tableSortColumnArrow } from "../utils/table-sort-a11y";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { downloadTextFile } from "../utils/download-text-file";
import {
  formatRabbitmqHostBucketDownload,
  RABBITMQ_HOST_BUCKET_DOWNLOAD_FILENAME,
} from "../utils/format-rabbitmq-host-bucket-download";

function formatAdminMonitorNumericStatistic(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" && Number.isFinite(value)) {
    return formatDecimalStandard(value);
  }
  return String(value);
}

const BADGE_MAP: Record<
  FreshnessBucket,
  { label: string; className: string }
> = {
  ok: { label: "OK (≤ 10 minutes)", className: "bg-emerald-600 text-white hover:bg-emerald-600" },
  gt_10min: { label: "> 10 minutes", className: "bg-cyan-400 text-black hover:bg-cyan-400" },
  gt_hour: { label: "> 1 hour", className: "bg-orange-500 text-black hover:bg-orange-500" },
  gt_day: { label: "> 1 day", className: "bg-red-600 text-white hover:bg-red-600" },
  gt_week: { label: "> 1 week", className: "bg-zinc-900 text-white hover:bg-zinc-900" },
};

const ROW_CLASS: Record<FreshnessBucket, string> = {
  ok: "bg-emerald-600/10",
  gt_10min: "bg-cyan-400/15",
  gt_hour: "bg-orange-500/15",
  gt_day: "bg-red-600/15",
  gt_week: "bg-zinc-900/10",
};

type AdminMonitorCollapsibleSectionProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  panelId: string;
  ariaLabel: string;
  title: ReactNode;
  children: ReactNode;
};

function AdminMonitorSectionRefreshButton({
  initialLoading,
  sectionBusy,
  onRefresh,
  trailing,
}: {
  initialLoading: boolean;
  sectionBusy: boolean;
  onRefresh: () => void;
  trailing?: ReactNode;
}) {
  if (initialLoading) return null;
  return (
    <div className="mb-2 flex justify-end gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="min-w-[110px]"
        onClick={onRefresh}
        disabled={sectionBusy}
      >
        {sectionBusy ? "Refreshing…" : "Refresh Data"}
      </Button>
      {trailing}
    </div>
  );
}

function AdminMonitorCollapsibleSection({
  open,
  onOpenChange,
  panelId,
  ariaLabel,
  title,
  children,
}: AdminMonitorCollapsibleSectionProps) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className="mb-4 rounded-[var(--radius)] border border-border">
      <CollapsibleTrigger
        render={
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-auto w-full justify-start rounded-t-[var(--radius)] rounded-b-none border-0 bg-muted px-3 py-2 font-semibold hover:bg-accent max-md:flex-wrap max-md:text-[0.95rem]"
          />
        }
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
          aria-hidden
        />
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent
        id={panelId}
        className="border-t border-border px-3 py-2 sm:px-4"
        role="region"
        aria-label={ariaLabel}
      >
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

const HOST_STATUS_ORDER: Record<FreshnessBucket, number> = {
  ok: 0,
  gt_10min: 1,
  gt_hour: 2,
  gt_day: 3,
  gt_week: 4,
};

const ADMIN_MONITOR_HOST_PAGE_SIZE = 150;

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

const INGEST_COVERAGE_GROUP_BY_LABELS: Record<string, string> = {
  signature: "Gap set",
  rack: "Rack",
  schema: "Schema variant",
  slug: "Capability slug",
};

const INGEST_COVERAGE_CLASS_LABELS: Record<IngestCoveragePairClass, string> = {
  unused: "Unused",
  type_detail_dropped: "Dropped from type panel",
  type_detail_only: "Type panel only",
  derived: "Used by metrics / plots",
};

const INGEST_COVERAGE_CLASS_ORDER: IngestCoveragePairClass[] = [
  "unused",
  "type_detail_dropped",
  "type_detail_only",
  "derived",
];

const INGEST_COVERAGE_DEFAULT_CLASSES: IngestCoveragePairClass[] = [
  "unused",
  "type_detail_dropped",
];

const INGEST_COVERAGE_STATUS_LABELS: Record<string, string> = {
  current: "Current",
  ingest_behind: "Ingest behind",
  unknown: "Unknown",
  incomplete: "Scan incomplete",
  no_recent_data: "No data in 7 days",
  not_scanned: "Not scanned",
};

const OPEN_SEGMENT_TOOLTIP =
  "Data still in the open segment is not ingestible until the monitor " +
  "rotates; this is normal.";

function formatEpochUtc(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${new Date(value * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function formatSecondsCompact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const total = Math.max(0, Math.round(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${total}s`;
}

function formatMix(mix: Record<string, number> | undefined): string {
  const entries = Object.entries(mix ?? {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return "—";
  const shown = entries.slice(0, 3).map(([k, v]) => `${k} (${v})`);
  return entries.length > 3 ? `${shown.join(", ")}, +${entries.length - 3}` : shown.join(", ");
}

function formatFraction(value: number | undefined): string {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function IngestCoveragePanel({
  coverage,
  busy,
  groupBy,
  onGroupByChange,
}: {
  coverage: AdminMonitorIngestCoverage;
  busy: boolean;
  groupBy: string;
  onGroupByChange: (mode: string) => void;
}) {
  const [classFilter, setClassFilter] = useState<IngestCoveragePairClass[]>(
    INGEST_COVERAGE_DEFAULT_CLASSES,
  );
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const state = coverage.sweeper_state ?? "sweeper_stale";
  const stale = state === "sweeper_stale";
  const groups: AdminMonitorIngestCoverageGroup[] = coverage.groups ?? [];
  const modes = coverage.group_by_modes ?? Object.keys(INGEST_COVERAGE_GROUP_BY_LABELS);

  const toggleClass = (cls: IngestCoveragePairClass) =>
    setClassFilter((cur) =>
      cur.includes(cls) ? cur.filter((c) => c !== cls) : [...cur, cls],
    );

  const handleCopyMarkdown = async () => {
    if (!coverage.markdown) return;
    const ok = await copyToClipboard(coverage.markdown);
    if (!ok) console.error("Failed to copy ingest coverage markdown");
  };

  return (
    <div
      className={cn("space-y-3", busy && "opacity-70")}
      aria-busy={busy || undefined}
      data-testid="ingest-coverage-panel"
    >
      <div className="flex flex-wrap items-start gap-2">
        <p className="text-sm text-muted-foreground flex-1 min-w-[16rem]">
          Compares the <code>(type, event)</code> pairs each host writes to{" "}
          <code>host_data</code> (last {coverage.window_minutes ?? 60} minutes
          before that host&apos;s newest sample, scanned in the background by{" "}
          <code>ingest_coverage_sweeper</code>) with the pairs update_metrics
          read for Job Detail in the last {coverage.consumed_fresh_days ?? 7}{" "}
          days.
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="ms-auto"
          disabled={!coverage.markdown}
          onClick={handleCopyMarkdown}
        >
          Copy as markdown
        </Button>
      </div>

      {state === "sweep_disabled" ? (
        <BannerErrorMessage
          variant="inline"
          message="Coverage sweep disabled on this site (ingest_coverage_sweep_enabled = no). No gap data is shown."
        />
      ) : null}
      {stale ? (
        <BannerErrorMessage
          variant="inline"
          message={`Coverage sweeper not running (last sweep finished ${formatEpochUtc(
            coverage.sweep?.finished_at,
          )}). Host rows below are stale.`}
        />
      ) : null}
      {state === "sweeping" ? (
        <p className="text-sm" role="status">
          Sweep in progress: {coverage.sweep?.hosts_done ?? 0} /{" "}
          {coverage.sweep?.hosts_total ?? 0} hosts.
        </p>
      ) : null}
      {state === "ok" ? (
        <p className="text-sm text-muted-foreground">
          Last sweep finished {formatEpochUtc(coverage.last_sweep_at)}.
        </p>
      ) : null}
      {coverage.incomplete ? (
        <BannerErrorMessage
          variant="inline"
          message={`Scan incomplete; results are not a healthy signal.${
            (coverage.errors ?? []).length > 0
              ? ` ${(coverage.errors ?? []).slice(0, 3).join(" ")}`
              : ""
          }`}
        />
      ) : null}
      {coverage.consumer_registry_empty && state !== "sweep_disabled" ? (
        <BannerErrorMessage
          variant="inline"
          message="update_metrics has not recorded reads yet; no gaps are listed."
        />
      ) : null}

      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label="Host status counts">
        {Object.entries(coverage.status_counts ?? {}).map(([key, count]) => (
          <div key={key} className="flex gap-1">
            <dt className="text-muted-foreground">
              {INGEST_COVERAGE_STATUS_LABELS[key] ?? key}:
            </dt>
            <dd className="font-medium">{count}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Group hosts by">
        <span className="text-sm font-medium">Group by:</span>
        {modes.map((mode) => (
          <Button
            key={mode}
            type="button"
            size="sm"
            variant={groupBy === mode ? "default" : "outline"}
            aria-pressed={groupBy === mode}
            onClick={() => onGroupByChange(mode)}
          >
            {INGEST_COVERAGE_GROUP_BY_LABELS[mode] ?? mode}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Pair classes shown">
        <span className="text-sm font-medium">Show:</span>
        {INGEST_COVERAGE_CLASS_ORDER.map((cls) => (
          <Button
            key={cls}
            type="button"
            size="sm"
            variant={classFilter.includes(cls) ? "default" : "outline"}
            aria-pressed={classFilter.includes(cls)}
            onClick={() => toggleClass(cls)}
          >
            {INGEST_COVERAGE_CLASS_LABELS[cls]}
          </Button>
        ))}
      </div>

      <Table className="border text-sm">
        <TableCaption>
          Host groups ({groups.length} of {coverage.groups_total ?? groups.length}
          ; {coverage.hosts_grouped ?? 0} hosts grouped of{" "}
          {coverage.hosts_total ?? 0}). The largest group is the baseline.
        </TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Group</TableHead>
            <TableHead scope="col">Hosts</TableHead>
            <TableHead scope="col">Slug / rack / schema mix</TableHead>
            <TableHead scope="col">Gaps</TableHead>
            <TableHead scope="col">Classes</TableHead>
            <TableHead scope="col">On baseline gap set</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="text-muted-foreground">
                No host groups to show.
              </TableCell>
            </TableRow>
          ) : (
            groups.map((group) => {
              const open = openGroup === group.key;
              return (
                <TableRow key={group.key}>
                  <TableCell>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-expanded={open}
                      onClick={() => setOpenGroup(open ? null : group.key)}
                    >
                      <ChevronRight
                        className={cn("size-3 transition-transform", open && "rotate-90")}
                        aria-hidden
                      />
                      <code>{group.key.slice(0, 16)}</code>
                      {group.is_baseline ? (
                        <Badge variant="secondary" className="ms-1">
                          baseline
                        </Badge>
                      ) : null}
                    </Button>
                  </TableCell>
                  <TableCell>{group.hosts}</TableCell>
                  <TableCell>
                    {formatMix(group.slugs)} / {formatMix(group.host_prefixes)} /{" "}
                    {formatMix(group.schema_variants)}
                  </TableCell>
                  <TableCell>{group.gap_count ?? 0}</TableCell>
                  <TableCell>
                    {INGEST_COVERAGE_CLASS_ORDER.map(
                      (cls) => `${INGEST_COVERAGE_CLASS_LABELS[cls]} ${group.class_counts?.[cls] ?? 0}`,
                    ).join(" · ")}
                  </TableCell>
                  <TableCell>{formatFraction(group.baseline_share)}</TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>

      {groups
        .filter((group) => group.key === openGroup)
        .map((group) => {
          const pairs = (group.pairs ?? []).filter((p) => classFilter.includes(p.class));
          return (
            <section
              key={group.key}
              className="space-y-3 rounded-[var(--radius)] border border-border p-3"
              aria-label={`Ingest coverage group ${group.key}`}
            >
              <Table className="border text-sm">
                <TableCaption>
                  Pairs in this group ({pairs.length} shown of{" "}
                  {group.pairs_total ?? pairs.length}).
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Type</TableHead>
                    <TableHead scope="col">Event</TableHead>
                    <TableHead scope="col">Class</TableHead>
                    <TableHead scope="col">Hosts</TableHead>
                    <TableHead scope="col">Stages</TableHead>
                    <TableHead scope="col">Last consumed</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pairs.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        No pairs in the selected classes.
                      </TableCell>
                    </TableRow>
                  ) : (
                    pairs.map((pair) => (
                      <TableRow key={`${pair.type}\0${pair.event}`}>
                        <TableCell>{pair.type}</TableCell>
                        <TableCell>{pair.event}</TableCell>
                        <TableCell>
                          <Badge variant={pair.class === "derived" ? "secondary" : "outline"}>
                            {INGEST_COVERAGE_CLASS_LABELS[pair.class] ?? pair.class}
                          </Badge>
                          {(pair.all_zero_fraction ?? 0) > 0 ? (
                            <span className="ms-1 text-muted-foreground">
                              (all-zero on {formatFraction(pair.all_zero_fraction)})
                            </span>
                          ) : null}
                        </TableCell>
                        <TableCell>{formatFraction(pair.host_fraction)}</TableCell>
                        <TableCell>{(pair.stages ?? []).join(", ") || "—"}</TableCell>
                        <TableCell>{formatEpochUtc(pair.last_consumed_at)}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              {(group.diff_vs_baseline ?? []).length > 0 ? (
                <Table className="border text-sm">
                  <TableCaption>Pairs that differ from the baseline group.</TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">Type</TableHead>
                      <TableHead scope="col">Event</TableHead>
                      <TableHead scope="col">This group</TableHead>
                      <TableHead scope="col">Baseline</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(group.diff_vs_baseline ?? []).map((d) => (
                      <TableRow key={`${d.type}\0${d.event}`}>
                        <TableCell>{d.type}</TableCell>
                        <TableCell>{d.event}</TableCell>
                        <TableCell>{formatFraction(d.host_fraction)}</TableCell>
                        <TableCell>{formatFraction(d.baseline_fraction)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : null}
              {(group.consumed_not_ingested ?? []).length > 0 ? (
                <p className="text-sm">
                  <span className="font-medium">Read by update_metrics, not sent here: </span>
                  {(group.consumed_not_ingested ?? [])
                    .map(([t, e]) => `${t}/${e}`)
                    .join(", ")}
                </p>
              ) : null}
              {(group.outlier_hosts ?? []).length > 0 ? (
                <div className="text-sm space-y-1">
                  <p className="font-medium">Outlier hosts</p>
                  <ul className="list-disc pl-5 space-y-1">
                    {(group.outlier_hosts ?? []).map((o) => (
                      <li key={o.fqdn}>
                        <code>{o.fqdn}</code>
                        {(o.missing_vs_group ?? []).length > 0
                          ? ` — missing ${(o.missing_vs_group ?? []).map(([t, e]) => `${t}/${e}`).join(", ")}`
                          : ""}
                        {(o.extra_gaps ?? []).length > 0
                          ? ` — extra gaps ${(o.extra_gaps ?? []).map(([t, e]) => `${t}/${e}`).join(", ")}`
                          : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <Table className="border text-sm">
                <TableCaption>Hosts in this group (capped).</TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Host</TableHead>
                    <TableHead scope="col">Status</TableHead>
                    <TableHead scope="col">Newest sample</TableHead>
                    <TableHead scope="col">Ingest backlog</TableHead>
                    <TableHead scope="col">
                      <span title={OPEN_SEGMENT_TOOLTIP}>Open segment age</span>
                    </TableHead>
                    <TableHead scope="col">Scanned</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(group.host_rows ?? []).map((row) => (
                    <TableRow
                      key={row.fqdn ?? ""}
                      className={cn(row.stale && "text-muted-foreground opacity-60")}
                      data-stale={row.stale ? "true" : undefined}
                    >
                      <TableCell>{row.fqdn}</TableCell>
                      <TableCell>
                        {INGEST_COVERAGE_STATUS_LABELS[row.status ?? ""] ?? row.status}
                        {row.stale ? " (stale)" : ""}
                      </TableCell>
                      <TableCell>{formatEpochUtc(row.newest_sample)}</TableCell>
                      <TableCell>{formatSecondsCompact(row.ingest_backlog_s)}</TableCell>
                      <TableCell title={OPEN_SEGMENT_TOOLTIP}>
                        {formatSecondsCompact(row.open_segment_age_s)}
                      </TableCell>
                      <TableCell>{formatEpochUtc(row.scanned_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          );
        })}
    </div>
  );
}

export default function AdminMonitor() {
  useDocumentTitle("HPCPerfStats Monitor");

  const [hostTimeExpanded, setHostTimeExpanded] = useState(false);
  const [rabbitHostTimeExpanded, setRabbitHostTimeExpanded] = useState(false);
  const [cacheExpanded, setCacheExpanded] = useState(false);
  const [rabbitExpanded, setRabbitExpanded] = useState(false);
  const [timescaledbExpanded, setTimescaledbExpanded] = useState(false);
  const [xaltExpanded, setXaltExpanded] = useState(false);
  const [telemetryHealthExpanded, setTelemetryHealthExpanded] = useState(false);
  const [showInformationalZeros, setShowInformationalZeros] = useState(false);
  const [hostRefreshSeq, setHostRefreshSeq] = useState(0);
  const [rabbitHostRefreshSeq, setRabbitHostRefreshSeq] = useState(0);
  const [cacheRefreshSeq, setCacheRefreshSeq] = useState(0);
  const [rabbitRefreshSeq, setRabbitRefreshSeq] = useState(0);
  const [timescaledbRefreshSeq, setTimescaledbRefreshSeq] = useState(0);
  const [xaltRefreshSeq, setXaltRefreshSeq] = useState(0);
  const [telemetryHealthRefreshSeq, setTelemetryHealthRefreshSeq] = useState(0);
  const [ingestCoverageExpanded, setIngestCoverageExpanded] = useState(false);
  const [ingestCoverageRefreshSeq, setIngestCoverageRefreshSeq] = useState(0);
  const [ingestCoverageGroupBy, setIngestCoverageGroupBy] = useState("signature");
  const [hostTablePage, setHostTablePage] = useState(1);
  const [rabbitHostTablePage, setRabbitHostTablePage] = useState(1);
  const { sort: hostSort, onSort: handleHostSort } = useTableSort("host", "asc", "asc");
  const {
    sort: rabbitHostSort,
    onSort: handleRabbitHostSort,
  } = useTableSort("host", "asc", "asc");
  const [xaltListMode, setXaltListMode] = useState("missing");
  const [nonRespondingHosts36, setNonRespondingHosts36] = useState("");

  const {
    data: hostStats,
    error: hostError,
    initialLoading: hostInitialLoading,
    sectionBusy: hostSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "hosts",
    enabled: hostTimeExpanded,
    refreshSeq: hostRefreshSeq,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.host_stats as AdminMonitorHostRow[] | undefined) || [],
  });

  const {
    data: rabbitHostStats,
    error: rabbitHostError,
    initialLoading: rabbitHostInitialLoading,
    sectionBusy: rabbitHostSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "rabbitmq_hosts",
    enabled: rabbitHostTimeExpanded,
    refreshSeq: rabbitHostRefreshSeq,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.rabbitmq_host_stats as AdminMonitorHostRow[] | undefined) || [],
  });

  const {
    data: cacheStats,
    error: cacheError,
    initialLoading: cacheInitialLoading,
    sectionBusy: cacheSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "cache",
    enabled: cacheExpanded,
    refreshSeq: cacheRefreshSeq,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.cache_stats as Record<string, unknown> | null | undefined) || null,
  });

  const {
    data: rabbitStats,
    error: rabbitError,
    initialLoading: rabbitInitialLoading,
    sectionBusy: rabbitSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "rabbitmq",
    enabled: rabbitExpanded,
    refreshSeq: rabbitRefreshSeq,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.rabbitmq_stats as Record<string, unknown> | null | undefined) || null,
  });

  const {
    data: timescaledbStats,
    error: timescaledbError,
    initialLoading: timescaledbInitialLoading,
    sectionBusy: timescaledbSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "timescaledb",
    enabled: timescaledbExpanded,
    refreshSeq: timescaledbRefreshSeq,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.timescaledb_stats as Record<string, unknown> | null | undefined) || null,
  });

  const {
    data: xaltStats,
    error: xaltError,
    initialLoading: xaltInitialLoading,
    sectionBusy: xaltSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "xalt",
    enabled: xaltExpanded,
    refreshSeq: xaltRefreshSeq,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.xalt_stats as AdminMonitorXaltStats | null | undefined) || null,
  });

  const {
    data: telemetryHealth,
    error: telemetryHealthError,
    initialLoading: telemetryHealthInitialLoading,
    sectionBusy: telemetryHealthSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "telemetry_health",
    enabled: telemetryHealthExpanded,
    refreshSeq: telemetryHealthRefreshSeq,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.telemetry_health as AdminMonitorTelemetryHealth | null | undefined) ||
      null,
  });

  const {
    data: ingestCoverage,
    error: ingestCoverageError,
    initialLoading: ingestCoverageInitialLoading,
    sectionBusy: ingestCoverageSectionBusy,
  } = useAdminMonitorSectionQuery({
    section: "ingest_coverage",
    enabled: ingestCoverageExpanded,
    refreshSeq: ingestCoverageRefreshSeq,
    groupBy: ingestCoverageGroupBy,
    pickResponse: (res: AdminMonitorSectionResponse) =>
      (res.ingest_coverage as AdminMonitorIngestCoverage | null | undefined) || null,
  });

  const ingestCoverageTitle = useMemo(() => {
    if (!ingestCoverage) return "Ingest vs Job Detail coverage";
    if (ingestCoverage.sweeper_state === "sweep_disabled") {
      return "Ingest vs Job Detail coverage — sweep disabled";
    }
    if (ingestCoverage.sweeper_state === "sweeper_stale") {
      return "Ingest vs Job Detail coverage — sweeper not running";
    }
    const gaps = (ingestCoverage.groups ?? []).reduce(
      (sum, g) => sum + (g.gap_count ?? 0),
      0,
    );
    return `Ingest vs Job Detail coverage — ${ingestCoverage.groups?.length ?? 0} groups, ${gaps} gap pairs`;
  }, [ingestCoverage]);

  const telemetryHealthTitle = useMemo(() => {
    if (!telemetryHealth) {
      return "Telemetry health (12h)";
    }
    if (telemetryHealth.timed_out) {
      return "Telemetry health (12h) — timed out";
    }
    const zeroCount = telemetryHealth.all_zero_events?.length ?? 0;
    const missingCount = telemetryHealth.missing_core_types?.length ?? 0;
    return `Telemetry health (12h) — ${zeroCount} actionable all-zero, ${missingCount} missing`;
  }, [telemetryHealth]);

  const informationalZeroFindings = useMemo(
    () =>
      (telemetryHealth?.findings ?? []).filter(
        (f) => f.kind === "all_zero_other_event",
      ),
    [telemetryHealth],
  );

  const handleCopyMonitorHandoff = async () => {
    const md = telemetryHealth?.monitor_handoff_markdown;
    if (!md) return;
    const ok = await copyToClipboard(md);
    if (!ok) console.error("Failed to copy monitor handoff report");
  };
  // Only show fully qualified hostnames (contain a dot) in the UI.
  const fqdnHostStats = useMemo(
    () => (hostStats ?? []).filter((row) => row.host && row.host.includes(".")),
    [hostStats],
  );
  const fqdnRabbitHostStats = useMemo(
    () => (rabbitHostStats ?? []).filter((row) => row.host && row.host.includes(".")),
    [rabbitHostStats],
  );

  // Build comma-separated list of FQDNs not seen in the past 36 hours when the
  // host section is open and hostStats are available.
  useEffect(() => {
    if (!hostTimeExpanded || hostInitialLoading || hostError) return;
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
  }, [hostTimeExpanded, hostInitialLoading, hostError, fqdnHostStats]);

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
    !hostInitialLoading && !hostError && fqdnHostStats.length > 0
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
    !rabbitHostInitialLoading && !rabbitHostError && fqdnRabbitHostStats.length > 0
      ? ` - Total hosts: ${rabbitHostTotal} · ${(Object.keys(BADGE_MAP) as FreshnessBucket[])
          .map((key) => `${BADGE_MAP[key].label}: ${rabbitHostBucketCounts[key] ?? 0}`)
          .join(" · ")}`
      : "";

  const xaltHeaderSummary =
    !xaltInitialLoading &&
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
  const hostTablePageCount = Math.max(
    1,
    Math.ceil(sortedHostStats.length / ADMIN_MONITOR_HOST_PAGE_SIZE),
  );
  const paginatedHostStats = useMemo(() => {
    const page = Math.min(hostTablePage, hostTablePageCount);
    const start = (page - 1) * ADMIN_MONITOR_HOST_PAGE_SIZE;
    return sortedHostStats.slice(start, start + ADMIN_MONITOR_HOST_PAGE_SIZE);
  }, [sortedHostStats, hostTablePage, hostTablePageCount]);
  const sortedRabbitHostStats = useMemo(
    () =>
      [...fqdnRabbitHostStats].sort((a, b) =>
        compareAdminMonitorHostRows(a, b, rabbitHostSort),
      ),
    [fqdnRabbitHostStats, rabbitHostSort],
  );
  const rabbitHostTablePageCount = Math.max(
    1,
    Math.ceil(sortedRabbitHostStats.length / ADMIN_MONITOR_HOST_PAGE_SIZE),
  );
  const paginatedRabbitHostStats = useMemo(() => {
    const page = Math.min(rabbitHostTablePage, rabbitHostTablePageCount);
    const start = (page - 1) * ADMIN_MONITOR_HOST_PAGE_SIZE;
    return sortedRabbitHostStats.slice(start, start + ADMIN_MONITOR_HOST_PAGE_SIZE);
  }, [sortedRabbitHostStats, rabbitHostTablePage, rabbitHostTablePageCount]);

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

      <AdminMonitorCollapsibleSection
        open={hostTimeExpanded}
        onOpenChange={setHostTimeExpanded}
        panelId="admin-monitor-host-time"
        ariaLabel="Most recent host data timestamps in database"
        title={`Most recent host data timestamps in database${hostHeaderSummary}`}
      >
          <AdminMonitorSectionRefreshButton
            initialLoading={hostInitialLoading}
            sectionBusy={hostSectionBusy}
            onRefresh={() => setHostRefreshSeq((s) => s + 1)}
          />
          {hostInitialLoading && <LoadingMessage message="Loading host timestamps…" />}
          {hostError && !hostInitialLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading host data: ${hostError}`}
            />
          )}
          {!hostInitialLoading && !hostError && (
            <>
              <div className="mb-2 flex flex-wrap items-center">
                <p className="mb-1 mr-3">
                  Status buckets:{" "}
                  {(Object.keys(BADGE_MAP) as FreshnessBucket[]).map((key) => (
                    <Badge key={key} className={BADGE_MAP[key].className}>
                      {BADGE_MAP[key].label}
                    </Badge>
                  ))}
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
              <div className="rounded-md border">
              <Table className="border-0 text-sm">
                <TableCaption className="sr-only">
                  Monitor agents reporting host data. Sort by host, last timestamp, or
                  status freshness using the column header buttons.
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col" aria-sort={tableSortAriaSort("host", hostSort.column, hostSort.direction)}>
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
                    </TableHead>
                    <TableHead
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
                    </TableHead>
                    <TableHead
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
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {paginatedHostStats.map((row, i) => {
                    const badge =
                      BADGE_MAP[(row.age_bucket as FreshnessBucket) || "gt_week"] ||
                      BADGE_MAP.gt_week;
                    const rowClass =
                      ROW_CLASS[(row.age_bucket as FreshnessBucket) || "gt_week"] || "";
                    return (
                      <TableRow key={`${row.host}-${i}`} className={rowClass}>
                        <TableCell>{row.host}</TableCell>
                        <TableCell>{formatHostTime(row.last_time)}</TableCell>
                        <TableCell>
                          <Badge className={badge.className}>{badge.label}</Badge>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {fqdnHostStats.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center">
                        No host data available.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
                </Table>
              </div>
                {hostTablePageCount > 1 ? (
                  <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="text-muted-foreground">
                      Showing{" "}
                      {(Math.min(hostTablePage, hostTablePageCount) - 1) *
                        ADMIN_MONITOR_HOST_PAGE_SIZE +
                        1}
                      –
                      {Math.min(
                        Math.min(hostTablePage, hostTablePageCount) *
                          ADMIN_MONITOR_HOST_PAGE_SIZE,
                        sortedHostStats.length,
                      )}{" "}
                      of {sortedHostStats.length} hosts
                    </span>
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={hostTablePage <= 1}
                        onClick={() => setHostTablePage((p) => Math.max(1, p - 1))}
                      >
                        Previous
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={hostTablePage >= hostTablePageCount}
                        onClick={() =>
                          setHostTablePage((p) => Math.min(hostTablePageCount, p + 1))
                        }
                      >
                        Next
                      </Button>
                    </div>
                  </div>
                ) : null}
            </>
          )}
      </AdminMonitorCollapsibleSection>

      <AdminMonitorCollapsibleSection
        open={xaltExpanded}
        onOpenChange={setXaltExpanded}
        panelId="admin-monitor-xalt-coverage"
        ariaLabel="XALT job coverage (last 3 days)"
        title={`XALT job coverage (last 3 days)${xaltHeaderSummary}`}
      >
          <AdminMonitorSectionRefreshButton
            initialLoading={xaltInitialLoading}
            sectionBusy={xaltSectionBusy}
            onRefresh={() => setXaltRefreshSeq((s) => s + 1)}
          />
          {xaltInitialLoading && <LoadingMessage message="Loading XALT coverage…" />}
          {xaltError && !xaltInitialLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading XALT coverage: ${xaltError}`}
            />
          )}
          {!xaltInitialLoading && !xaltError && xaltStats && (
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
                    <Select
                      value={xaltListMode}
                      onValueChange={(value) => {
                        if (value) setXaltListMode(value);
                      }}
                    >
                      <SelectTrigger id="xaltListMode" className="h-7 w-auto max-w-md">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="missing">
                          Missing JIDs ({String(xaltStats.jids_missing_xalt_data ?? 0)})
                        </SelectItem>
                        <SelectItem value="found">
                          Found JIDs ({String(xaltStats.jids_with_xalt_data ?? 0)})
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {xaltListMode === "missing" && (xaltStats.jids_missing_xalt_data ?? 0) > 0 && (
                    <Table className="border text-sm">
                        <TableCaption className="sr-only">
                          Job IDs from the last three days that are missing XALT coverage.
                          List may be truncated.
                        </TableCaption>
                        <TableHeader>
                          <TableRow>
                            <TableHead scope="col">Missing JIDs (truncated)</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {Array.isArray(xaltStats.missing_jids) &&
                            xaltStats.missing_jids.length > 0 &&
                            xaltStats.missing_jids.map((jid, i) => (
                              <TableRow key={`${jid}-${i}`}>
                                <TableCell>{jid}</TableCell>
                              </TableRow>
                            ))}
                          {(!Array.isArray(xaltStats.missing_jids) ||
                            xaltStats.missing_jids.length === 0) && (
                            <TableRow>
                              <TableCell className="text-muted-foreground text-center">
                                No missing JIDs listed.
                              </TableCell>
                            </TableRow>
                          )}
                          {xaltStats.missing_jids_truncated && (
                            <TableRow>
                              <TableCell className="text-muted-foreground">
                                Showing first{" "}
                                {String(
                                  xaltStats.missing_jids_limit ?? "—"
                                )}{" "}
                                missing JIDs.
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                  )}

                  {xaltListMode === "found" && (xaltStats.jids_with_xalt_data ?? 0) > 0 && (
                    <Table className="border text-sm">
                        <TableCaption className="sr-only">
                          Job IDs from the last three days that have XALT coverage. List may
                          be truncated.
                        </TableCaption>
                        <TableHeader>
                          <TableRow>
                            <TableHead scope="col">Found JIDs (truncated)</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {Array.isArray(xaltStats.found_jids) &&
                            xaltStats.found_jids.length > 0 &&
                            xaltStats.found_jids.map((jid, i) => (
                              <TableRow key={`${jid}-${i}`}>
                                <TableCell>{jid}</TableCell>
                              </TableRow>
                            ))}
                          {(!Array.isArray(xaltStats.found_jids) ||
                            xaltStats.found_jids.length === 0) && (
                            <TableRow>
                              <TableCell className="text-muted-foreground text-center">
                                No found JIDs listed.
                              </TableCell>
                            </TableRow>
                          )}
                          {xaltStats.found_jids_truncated && (
                            <TableRow>
                              <TableCell className="text-muted-foreground">
                                Showing first{" "}
                                {String(xaltStats.found_jids_limit ?? "—")} found JIDs.
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
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
          {!xaltInitialLoading && !xaltError && !xaltStats && (
            <div className="text-muted-foreground">No XALT coverage statistics available.</div>
          )}
      </AdminMonitorCollapsibleSection>

      <AdminMonitorCollapsibleSection
        open={rabbitHostTimeExpanded}
        onOpenChange={setRabbitHostTimeExpanded}
        panelId="admin-monitor-rabbit-host-time"
        ariaLabel="Most recent host data timestamps in RabbitMQ"
        title={`Most recent host data timestamps in RabbitMQ${rabbitHostHeaderSummary}`}
      >
          <AdminMonitorSectionRefreshButton
            initialLoading={rabbitHostInitialLoading}
            sectionBusy={rabbitHostSectionBusy}
            onRefresh={() => setRabbitHostRefreshSeq((s) => s + 1)}
            trailing={
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-w-[110px]"
                disabled={rabbitHostSectionBusy}
                aria-label="Download RabbitMQ host freshness buckets"
                onClick={() =>
                  downloadTextFile(
                    RABBITMQ_HOST_BUCKET_DOWNLOAD_FILENAME,
                    formatRabbitmqHostBucketDownload(fqdnRabbitHostStats),
                  )
                }
              >
                Download
              </Button>
            }
          />
          {rabbitHostInitialLoading && (
            <LoadingMessage message="Loading RabbitMQ host timestamps…" />
          )}
          {rabbitHostError && !rabbitHostInitialLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading RabbitMQ host data: ${rabbitHostError}`}
            />
          )}
          {!rabbitHostInitialLoading && !rabbitHostError && (
            <>
            <div className="rounded-md border">
            <Table className="border-0 text-sm">
                <TableCaption className="sr-only">
                  Hosts seen via RabbitMQ plus last-7-day job-table hosts with no
                  Valkey recent_host key, and their last data timestamps. Sort by
                  host, last timestamp, or status freshness using the column
                  header buttons.
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead
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
                    </TableHead>
                    <TableHead
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
                    </TableHead>
                    <TableHead
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
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {paginatedRabbitHostStats.map((row, i) => {
                    const badge =
                      BADGE_MAP[(row.age_bucket as FreshnessBucket) || "gt_week"] ||
                      BADGE_MAP.gt_week;
                    const rowClass =
                      ROW_CLASS[(row.age_bucket as FreshnessBucket) || "gt_week"] || "";
                    return (
                      <TableRow key={`${row.host}-${i}`} className={rowClass}>
                        <TableCell>{row.host}</TableCell>
                        <TableCell>{formatHostTime(row.last_time)}</TableCell>
                        <TableCell>
                          <Badge className={badge.className}>{badge.label}</Badge>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {fqdnRabbitHostStats.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center">
                        No RabbitMQ host data available.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
              {rabbitHostTablePageCount > 1 ? (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-muted-foreground">
                    Showing{" "}
                    {(Math.min(rabbitHostTablePage, rabbitHostTablePageCount) - 1) *
                      ADMIN_MONITOR_HOST_PAGE_SIZE +
                      1}
                    –
                    {Math.min(
                      Math.min(rabbitHostTablePage, rabbitHostTablePageCount) *
                        ADMIN_MONITOR_HOST_PAGE_SIZE,
                      sortedRabbitHostStats.length,
                    )}{" "}
                    of {sortedRabbitHostStats.length} hosts
                  </span>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={rabbitHostTablePage <= 1}
                      onClick={() => setRabbitHostTablePage((p) => Math.max(1, p - 1))}
                    >
                      Previous
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={rabbitHostTablePage >= rabbitHostTablePageCount}
                      onClick={() =>
                        setRabbitHostTablePage((p) =>
                          Math.min(rabbitHostTablePageCount, p + 1),
                        )
                      }
                    >
                      Next
                    </Button>
                  </div>
                </div>
              ) : null}
            </>
          )}
      </AdminMonitorCollapsibleSection>

      <AdminMonitorCollapsibleSection
        open={timescaledbExpanded}
        onOpenChange={setTimescaledbExpanded}
        panelId="admin-monitor-timescaledb-stats"
        ariaLabel="TimescaleDB statistics"
        title="TimescaleDB statistics"
      >
          <AdminMonitorSectionRefreshButton
            initialLoading={timescaledbInitialLoading}
            sectionBusy={timescaledbSectionBusy}
            onRefresh={() => setTimescaledbRefreshSeq((s) => s + 1)}
          />
          {timescaledbInitialLoading && (
            <LoadingMessage message="Loading TimescaleDB statistics…" />
          )}
          {timescaledbError && !timescaledbInitialLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading TimescaleDB stats: ${timescaledbError}`}
            />
          )}
          {!timescaledbInitialLoading && !timescaledbError && timescaledbStats && (
            <Table className="border text-sm">
              <TableCaption className="sr-only">
                TimescaleDB database and hypertable size statistics.
              </TableCaption>
              <TableBody>
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
                      <TableRow key={key}>
                        <TableHead scope="row">{label}</TableHead>
                        <TableCell>
                          {typeof timescaledbStats[key] === "number"
                            ? formatAdminMonitorNumericStatistic(timescaledbStats[key])
                            : String(timescaledbStats[key])}
                        </TableCell>
                      </TableRow>
                    ));
                })()}
                {(!timescaledbStats ||
                  Object.entries(timescaledbStats).filter(
                    ([, value]) => value !== null && value !== undefined
                  ).length === 0) && (
                  <TableRow>
                    <TableCell colSpan={2} className="text-muted-foreground">
                      No TimescaleDB statistics available.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
              </Table>
          )}
          {!timescaledbInitialLoading && !timescaledbError && !timescaledbStats && (
            <div className="text-muted-foreground">No TimescaleDB statistics available.</div>
          )}
      </AdminMonitorCollapsibleSection>

      <AdminMonitorCollapsibleSection
        open={cacheExpanded}
        onOpenChange={setCacheExpanded}
        panelId="admin-monitor-cache-stats"
        ariaLabel="Cache and Valkey statistics"
        title="Cache / Valkey statistics"
      >
          <AdminMonitorSectionRefreshButton
            initialLoading={cacheInitialLoading}
            sectionBusy={cacheSectionBusy}
            onRefresh={() => setCacheRefreshSeq((s) => s + 1)}
          />
          {cacheInitialLoading && <LoadingMessage message="Loading cache statistics…" />}
          {cacheError && !cacheInitialLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading cache stats: ${cacheError}`}
            />
          )}
          {!cacheInitialLoading && !cacheError && cacheStats && Object.keys(cacheStats).length > 0 && (
            <Table className="border text-sm">
              <TableCaption className="sr-only">
                Cache and Valkey key statistics for the application.
              </TableCaption>
              <TableBody>
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
                    <TableRow key={key}>
                      <TableHead scope="row">{key}</TableHead>
                      <TableCell>{displayValue}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
              </Table>
          )}
          {!cacheInitialLoading && !cacheError && (!cacheStats || Object.keys(cacheStats).length === 0) && (
            <div className="text-muted-foreground">No cache statistics available.</div>
          )}
      </AdminMonitorCollapsibleSection>

      <AdminMonitorCollapsibleSection
        open={rabbitExpanded}
        onOpenChange={setRabbitExpanded}
        panelId="admin-monitor-rabbitmq-stats"
        ariaLabel="RabbitMQ statistics"
        title="RabbitMQ statistics"
      >
          <AdminMonitorSectionRefreshButton
            initialLoading={rabbitInitialLoading}
            sectionBusy={rabbitSectionBusy}
            onRefresh={() => setRabbitRefreshSeq((s) => s + 1)}
          />
          {rabbitInitialLoading && (
            <LoadingMessage message="Loading RabbitMQ statistics…" />
          )}
          {rabbitError && !rabbitInitialLoading && (
            <BannerErrorMessage
              variant="inline"
              message={`Error loading RabbitMQ stats: ${rabbitError}`}
            />
          )}
          {!rabbitInitialLoading && !rabbitError && rabbitStats && (
            <>
              {rabbitStats.error && (
                <BannerErrorMessage
                  variant="inline"
                  className="mb-2 text-destructive"
                  message={`RabbitMQ reported an error: ${rabbitStats.error}`}
                />
              )}
              <Table className="border text-sm">
                <TableCaption className="sr-only">
                  RabbitMQ queue depth, consumer, message volume, rates, and node
                  memory/disk/alarm statistics.
                </TableCaption>
                <TableBody>
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
                      messages_acked_total:
                        "Messages acknowledged (total since broker start)",
                      messages_redelivered_total:
                        "Messages redelivered (total since broker start)",
                      messages_publish_rate: "Publish rate (msg/s)",
                      messages_deliver_rate: "Deliver rate (msg/s)",
                      messages_ack_rate: "Ack rate (msg/s)",
                      messages_redeliver_rate: "Redeliver rate (msg/s)",
                      messages_published_since_snapshot:
                        "Messages published since previous snapshot",
                      snapshot_hours: "Hours covered by previous snapshot window",
                      messages_published_last_24h_estimate:
                        "Approx. messages published in last 24 hours",
                      node_name: "Node name",
                      mem_used: "Erlang memory used (bytes)",
                      mem_limit: "Erlang memory limit (bytes)",
                      disk_free: "Disk free (bytes)",
                      disk_free_limit: "Disk free alarm limit (bytes)",
                      alarms: "Node alarms",
                      erlang_version: "Erlang version",
                    };
                    return Object.entries(rabbitStats)
                      .filter(([key, value]) => key in LABELS && value !== null && value !== undefined)
                      .map(([key, value]) => (
                        <TableRow key={key}>
                          <TableHead scope="row">{LABELS[key]}</TableHead>
                          <TableCell>{formatAdminMonitorNumericStatistic(value)}</TableCell>
                        </TableRow>
                      ));
                  })()}
                  {(!rabbitStats ||
                    Object.entries(rabbitStats).filter(
                      ([_key, value]) => value !== null && value !== undefined
                    ).length === 0) && (
                    <TableRow>
                      <TableCell colSpan={2} className="text-muted-foreground">
                        No RabbitMQ statistics available.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
                </Table>
            </>
          )}
          {!rabbitInitialLoading &&
            !rabbitError &&
            !rabbitStats && (
              <div className="text-muted-foreground">No RabbitMQ statistics available.</div>
            )}
      </AdminMonitorCollapsibleSection>

      <AdminMonitorCollapsibleSection
        open={telemetryHealthExpanded}
        onOpenChange={setTelemetryHealthExpanded}
        panelId="admin-monitor-telemetry-health"
        ariaLabel="Telemetry health over the last 12 hours"
        title={telemetryHealthTitle}
      >
        <AdminMonitorSectionRefreshButton
          initialLoading={telemetryHealthInitialLoading}
          sectionBusy={telemetryHealthSectionBusy}
          onRefresh={() => setTelemetryHealthRefreshSeq((s) => s + 1)}
        />
        {telemetryHealthInitialLoading && (
          <LoadingMessage message="Loading telemetry health…" />
        )}
        {telemetryHealthError && !telemetryHealthInitialLoading && (
          <BannerErrorMessage
            variant="inline"
            message={`Error loading telemetry health: ${telemetryHealthError}`}
          />
        )}
        {!telemetryHealthInitialLoading &&
          !telemetryHealthError &&
          telemetryHealth && (
            <div
              className={cn(
                "space-y-3",
                telemetryHealthSectionBusy && "opacity-70",
              )}
              aria-busy={telemetryHealthSectionBusy || undefined}
            >
              <div className="flex flex-wrap items-start gap-2">
                <p className="text-sm text-muted-foreground flex-1 min-w-[16rem]">
                  Bounded scan of non-error <code>host_data</code>{" "}
                  <code>(type, event)</code> pairs over the last{" "}
                  {telemetryHealth.window_hours ?? 12} hours, sampling recently
                  reporting hosts from Valkey
                  {typeof telemetryHealth.ok_summary?.hosts_sampled === "number"
                    ? ` (${telemetryHealth.ok_summary.hosts_sampled} host${
                        telemetryHealth.ok_summary.hosts_sampled === 1 ? "" : "s"
                      })`
                    : ""}
                  . Default tables show actionable core missing/all-zero only;
                  idle IB/GPU/PMC zeros are informational. This is an
                  investigation signal after a monitor deploy, not a deployment
                  gate.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="ms-auto"
                  disabled={!telemetryHealth.monitor_handoff_markdown}
                  onClick={handleCopyMonitorHandoff}
                >
                  Copy monitor handoff report
                </Button>
              </div>
              {(telemetryHealth.hosts_sampled_fqdns ?? []).length > 0 ? (
                <p className="text-sm">
                  <span className="font-medium">Sampled FQDNs: </span>
                  {(telemetryHealth.hosts_sampled_fqdns ?? []).join(", ")}
                </p>
              ) : null}
              <div className="text-sm space-y-1">
                <p className="font-medium">Monitor signatures</p>
                {(telemetryHealth.monitor_identities ?? []).length === 0 ? (
                  <p className="text-muted-foreground">
                    No Valkey monitor identity for sampled hosts (slug pending
                    RPM / identity not yet written).
                  </p>
                ) : (
                  <ul className="list-disc pl-5 space-y-1">
                    {(telemetryHealth.monitor_identities ?? []).map((ident) => {
                      const slug =
                        ident.capability_slug || "slug pending RPM";
                      return (
                        <li key={ident.fqdn ?? slug}>
                          <code>{ident.fqdn}</code> — version{" "}
                          <code>{ident.package_version ?? "unknown"}</code>,
                          build <code>{slug}</code>
                          {ident.uname ? (
                            <>
                              , uname <code>{ident.uname}</code>
                            </>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              {telemetryHealth.timed_out || telemetryHealth.error ? (
                <BannerErrorMessage
                  variant="inline"
                  message={
                    telemetryHealth.error ||
                    "Telemetry health query timed out; results are incomplete."
                  }
                />
              ) : null}
              {telemetryHealth.ok_summary?.scanned_note ? (
                <p className="text-sm">{telemetryHealth.ok_summary.scanned_note}</p>
              ) : null}
              {telemetryHealth.truncated ? (
                <p className="text-sm text-muted-foreground">
                  Actionable all-zero list truncated at the hard cap.
                </p>
              ) : null}
              <Table className="border text-sm">
                <TableCaption>
                  Actionable all-zero core type/event pairs (excluding names
                  containing &quot;error&quot;).
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Type</TableHead>
                    <TableHead scope="col">Event</TableHead>
                    <TableHead scope="col">Row count</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(telemetryHealth.all_zero_events ?? []).length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-muted-foreground">
                        {telemetryHealth.timed_out
                          ? "No all-zero results (query incomplete)."
                          : "No actionable all-zero type/event pairs in the window."}
                      </TableCell>
                    </TableRow>
                  ) : (
                    (telemetryHealth.all_zero_events ?? []).map((row) => (
                      <TableRow key={`${row.type}\0${row.event}`}>
                        <TableCell>{row.type}</TableCell>
                        <TableCell>{row.event}</TableCell>
                        <TableCell>
                          {formatAdminMonitorNumericStatistic(row.row_count)}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              <Table className="border text-sm">
                <TableCaption>
                  Expected core monitor types with zero rows in the window.
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Missing core type</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(telemetryHealth.missing_core_types ?? []).length === 0 ? (
                    <TableRow>
                      <TableCell className="text-muted-foreground">
                        {telemetryHealth.timed_out
                          ? "No missing-type results (query incomplete)."
                          : "No missing core types."}
                      </TableCell>
                    </TableRow>
                  ) : (
                    (telemetryHealth.missing_core_types ?? []).map((typeName) => (
                      <TableRow key={typeName}>
                        <TableCell>{typeName}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              {informationalZeroFindings.length > 0 ? (
                <div className="space-y-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setShowInformationalZeros((open) => !open)
                    }
                  >
                    {showInformationalZeros ? "Hide" : "Show"}{" "}
                    {informationalZeroFindings.length} informational all-zero
                    pair{informationalZeroFindings.length === 1 ? "" : "s"}
                  </Button>
                  {showInformationalZeros ? (
                    <Table className="border text-sm">
                      <TableCaption>
                        Informational all-zero pairs (idle/sparse may be
                        normal).
                      </TableCaption>
                      <TableHeader>
                        <TableRow>
                          <TableHead scope="col">Type</TableHead>
                          <TableHead scope="col">Event</TableHead>
                          <TableHead scope="col">Row count</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {informationalZeroFindings.map((row) => (
                          <TableRow
                            key={`${row.type ?? ""}\0${row.event ?? ""}`}
                          >
                            <TableCell>{row.type}</TableCell>
                            <TableCell>{row.event}</TableCell>
                            <TableCell>
                              {formatAdminMonitorNumericStatistic(
                                row.row_count,
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : null}
                </div>
              ) : null}
            </div>
          )}
        {!telemetryHealthInitialLoading &&
          !telemetryHealthError &&
          !telemetryHealth && (
            <div className="text-muted-foreground">
              No telemetry health data available.
            </div>
          )}
      </AdminMonitorCollapsibleSection>

      <AdminMonitorCollapsibleSection
        open={ingestCoverageExpanded}
        onOpenChange={setIngestCoverageExpanded}
        panelId="admin-monitor-ingest-coverage"
        ariaLabel="Ingest versus Job Detail coverage"
        title={ingestCoverageTitle}
      >
        <AdminMonitorSectionRefreshButton
          initialLoading={ingestCoverageInitialLoading}
          sectionBusy={ingestCoverageSectionBusy}
          onRefresh={() => setIngestCoverageRefreshSeq((s) => s + 1)}
        />
        {ingestCoverageInitialLoading && (
          <LoadingMessage message="Loading ingest coverage…" />
        )}
        {ingestCoverageError && !ingestCoverageInitialLoading && (
          <BannerErrorMessage
            variant="inline"
            message={`Error loading ingest coverage: ${ingestCoverageError}`}
          />
        )}
        {!ingestCoverageInitialLoading && !ingestCoverageError && ingestCoverage && (
          <IngestCoveragePanel
            coverage={ingestCoverage}
            busy={ingestCoverageSectionBusy}
            groupBy={ingestCoverageGroupBy}
            onGroupByChange={setIngestCoverageGroupBy}
          />
        )}
        {!ingestCoverageInitialLoading && !ingestCoverageError && !ingestCoverage && (
          <div className="text-muted-foreground">No ingest coverage data available.</div>
        )}
      </AdminMonitorCollapsibleSection>
    </>
  );
}
