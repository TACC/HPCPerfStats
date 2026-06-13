import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/api";
import type { JobMonitorRow } from "@/types/view-models";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import SortableTableHeader from "../components/SortableTableHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDecimalStandard } from "../utils/formatDecimal";
import {
  JOB_MONITOR_GPU_NO_DATA_ROW,
  jobMonitorSortComparable,
  patchJobMonitorGpuRowByUsername,
} from "../utils/job-monitor-gpu";
import { useTableSort } from "../hooks/useTableSort";
import { useDocumentTitle } from "../utils/useDocumentTitle";

type GpuLoadingState = "loading" | "loaded" | "no_data";

type JobMonitorViewRow = JobMonitorRow & {
  username?: string;
  total_jobs?: unknown;
  failed_jobs?: unknown;
  failed_rate?: unknown;
  timedout_jobs?: unknown;
  timedout_rate?: unknown;
  gpu_count_total?: unknown;
  gpu_active_total?: unknown;
  gpu_active_percentage?: unknown;
  gpuLoadingState: GpuLoadingState;
};

type JobMonitorApiResponse = {
  results?: unknown;
  window_days?: unknown;
};

type JobMonitorGpuResponse = {
  has_data?: unknown;
  gpu_count_total?: unknown;
  gpu_active_total?: unknown;
  gpu_active_percentage?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function normalizeJobMonitorRows(rawRows: unknown): JobMonitorViewRow[] {
  if (!Array.isArray(rawRows)) return [];
  return rawRows.map((rawRow) => {
    const row = isRecord(rawRow) ? rawRow : {};
    return {
      ...row,
      gpu_count_total: null,
      gpu_active_total: null,
      gpu_active_percentage: null,
      gpuLoadingState: "loading",
    };
  });
}

function getApiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && typeof error.message === "string" && error.message.trim()) {
    return error.message;
  }
  return fallback;
}

const sortHeaderClassName =
  "job-monitor-sort-header h-auto p-0 text-left font-medium text-foreground no-underline hover:no-underline";

export default function JobMonitor() {
  useDocumentTitle("Job failure monitor");

  const [rows, setRows] = useState<JobMonitorViewRow[]>([]);
  const [windowDays, setWindowDays] = useState(30);
  const [inputDays, setInputDays] = useState("30");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { sort, onSort } = useTableSort("failed_rate", "desc", "desc");
  const sortKey = sort.column;
  const sortDir = sort.direction;

  const formatGpuValue = (value: unknown, loadingState: GpuLoadingState) => {
    if (loadingState === "loading") return "Loading";
    if (value === null || value === undefined || value === "") return "N/A";
    const n = Number(value);
    if (!Number.isFinite(n)) return "N/A";
    return formatDecimalStandard(n);
  };

  const formatGpuPercentValue = (value: unknown, loadingState: GpuLoadingState) => {
    if (loadingState === "loading") return "Loading";
    const formatted = formatGpuValue(value, "loaded");
    return formatted === "N/A" ? "N/A" : `${formatted}%`;
  };

  const loadGpuRowsAsync = (rowsData: JobMonitorViewRow[], daysForWindow?: number) => {
    (rowsData || []).forEach((row) => {
      const username = String(row.username || "");
      if (!username) {
        setRows((prev) =>
          patchJobMonitorGpuRowByUsername(
            prev,
            username,
            JOB_MONITOR_GPU_NO_DATA_ROW,
          ) as JobMonitorViewRow[],
        );
        return;
      }
      (api.getJobMonitorGpuForUser(username, daysForWindow) as Promise<unknown>)
        .then((gpuRes) => {
          const gpu = (isRecord(gpuRes) ? gpuRes : {}) as JobMonitorGpuResponse;
          const hasData = gpu.has_data === true;
          const patch = hasData
            ? {
                gpu_count_total: gpu.gpu_count_total,
                gpu_active_total: gpu.gpu_active_total,
                gpu_active_percentage: gpu.gpu_active_percentage,
                gpuLoadingState: "loaded" as const,
              }
            : JOB_MONITOR_GPU_NO_DATA_ROW;
          setRows((prev) =>
            patchJobMonitorGpuRowByUsername(prev, username, patch) as JobMonitorViewRow[],
          );
        })
        .catch(() => {
          setRows((prev) =>
            patchJobMonitorGpuRowByUsername(
              prev,
              username,
              JOB_MONITOR_GPU_NO_DATA_ROW,
            ) as JobMonitorViewRow[],
          );
        });
    });
  };

  const loadData = (daysOverride?: number) => {
    setLoading(true);
    setError(null);
    (api.getJobMonitor(daysOverride) as Promise<unknown>)
      .then((res) => {
        const typed = (isRecord(res) ? res : {}) as JobMonitorApiResponse;
        const nextRows = normalizeJobMonitorRows(typed.results);
        setRows(nextRows);
        if (typeof typed.window_days === "number") {
          setWindowDays(typed.window_days);
          setInputDays(String(typed.window_days));
          loadGpuRowsAsync(nextRows, typed.window_days);
        } else {
          loadGpuRowsAsync(nextRows, daysOverride);
        }
      })
      .catch((e: unknown) =>
        setError(getApiErrorMessage(e, "Unable to load job monitor data.")),
      )
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sortedRows = [...rows].sort((a, b) => {
    const av = jobMonitorSortComparable(a, sortKey);
    const bv = jobMonitorSortComparable(b, sortKey);
    if (av < bv) return sortDir === "asc" ? -1 : 1;
    if (av > bv) return sortDir === "asc" ? 1 : -1;
    const au = (a.username || "").toLowerCase();
    const bu = (b.username || "").toLowerCase();
    if (au < bu) return -1;
    if (au > bu) return 1;
    return 0;
  });

  return (
    <>
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">Job failure monitor</h1>
      <p className="mb-3 text-muted-foreground">
        Aggregated job outcomes by user for the last {windowDays} days. Only users
        who have run more than {windowDays / 2} jobs in this window are included.
      </p>
      <form
        className="mb-3 flex flex-wrap items-end gap-3"
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
        <div className="space-y-1">
          <Label htmlFor="job-monitor-days">Window (days):</Label>
          <Input
            id="job-monitor-days"
            type="number"
            min="1"
            max="365"
            className="w-24"
            value={inputDays}
            onChange={(e) => setInputDays(e.target.value)}
          />
        </div>
        <Button type="submit" variant="outline" size="sm">
          Apply
        </Button>
      </form>
      {loading && <LoadingMessage message="Loading job monitor data…" />}
      {error && !loading && (
        <BannerErrorMessage
          variant="inline"
          className="mb-3 text-destructive"
          message={`Error loading job monitor data: ${error}`}
        />
      )}
      {!loading && !error && (
        <Table className="border text-sm">
          <TableCaption className="sr-only">
            Job outcomes by user for the last {windowDays} days
          </TableCaption>
          <TableHeader>
            <TableRow>
              <SortableTableHeader
                column="username"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                User
              </SortableTableHeader>
              <SortableTableHeader
                column="total_jobs"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                Number of jobs
              </SortableTableHeader>
              <SortableTableHeader
                column="failed_jobs"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                Number of failed jobs
              </SortableTableHeader>
              <SortableTableHeader
                column="failed_rate"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                % failed
              </SortableTableHeader>
              <SortableTableHeader
                column="timedout_jobs"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                Number of timed out jobs
              </SortableTableHeader>
              <SortableTableHeader
                column="timedout_rate"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                % timed out
              </SortableTableHeader>
              <SortableTableHeader
                column="gpu_count_total"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                Total GPUs Allocated
              </SortableTableHeader>
              <SortableTableHeader
                column="gpu_active_total"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                Number of GPUs Active
              </SortableTableHeader>
              <SortableTableHeader
                column="gpu_active_percentage"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                buttonClassName={sortHeaderClassName}
              >
                Percentage of GPUs Active
              </SortableTableHeader>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedRows.map((row) => (
              <TableRow key={row.username || "(unknown)"}>
                <TableCell>
                  {row.username ? (
                    <Link href={`/machine/username/${encodeURIComponent(row.username)}/`} className="text-primary hover:underline">
                      {row.username}
                    </Link>
                  ) : (
                    "(unknown)"
                  )}
                </TableCell>
                <TableCell>{formatDecimalStandard(row.total_jobs)}</TableCell>
                <TableCell>{formatDecimalStandard(row.failed_jobs)}</TableCell>
                <TableCell>{formatDecimalStandard(row.failed_rate)}</TableCell>
                <TableCell>{formatDecimalStandard(row.timedout_jobs)}</TableCell>
                <TableCell>{formatDecimalStandard(row.timedout_rate)}</TableCell>
                <TableCell>{formatGpuValue(row.gpu_count_total, row.gpuLoadingState)}</TableCell>
                <TableCell>{formatGpuValue(row.gpu_active_total, row.gpuLoadingState)}</TableCell>
                <TableCell>
                  {formatGpuPercentValue(
                    row.gpu_active_percentage,
                    row.gpuLoadingState,
                  )}
                </TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="text-center text-muted-foreground">
                  No jobs found in the selected time window.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      )}
    </>
  );
}
