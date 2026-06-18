import {
  EXTENDED_SEARCH_ALLOWED_PARAM_NAMES,
  getExtendedSearchParameterDefinition,
} from "./extended-search-parameters";
import { mergeRouteParamsIntoSearchParams } from "./job-list-header-filter-params";
import { formatJobListSortSummaryLine } from "./job-list-sort-labels";
import { getJobMetricShortLabel } from "./jobMetricDisplayLabels";
import { PROJECT_FIELD_LABEL } from "./site-field-labels";

const GTE_OPS: Record<string, string> = {
  runtime__gte: "≥",
  nhosts__gte: "≥",
  node_hrs__gte: "≥",
  end_time__gte: "on or after",
};

const LTE_OPS: Record<string, string> = {
  runtime__lte: "≤",
  nhosts__lte: "≤",
  node_hrs__lte: "≤",
  end_time__lte: "on or before",
};

const ROUTE_ONLY_KEYS = new Set(["page", "order_by"]);
const HANDLED_IN_EXTENDED_LOOP = new Set(["end_time__date"]);

function formatMetricFilterLine(metricKey: string, op: string, value: string) {
  const label = getJobMetricShortLabel(metricKey);
  const sym = op === "gte" ? "≥" : "≤";
  return `${label} ${sym} ${value}`;
}

function isYearToken(value: string): boolean {
  return value.length === 4 && /^\d{4}$/.test(value);
}

function formatEndTimeDateLine(value: string): string {
  if (isYearToken(value)) {
    return `Calendar year: ${value}`;
  }
  return `Job end date: ${value}`;
}

function formatMultiValueLine(label: string, raw: string): string {
  const values = raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (!values.length) return "";
  if (values.length === 1) return `${label}: ${values[0]}`;
  return `${label}: ${values.join(", ")}`;
}

function formatHeaderOnlyParams(params: URLSearchParams, lines: string[]): void {
  const account = params.get("account");
  if (account?.trim() && !params.get("account__icontains")) {
    lines.push(formatMultiValueLine(PROJECT_FIELD_LABEL, account));
  }
  const performance = params.get("performance_sort_rank");
  if (performance?.trim()) {
    lines.push(formatMultiValueLine("Performance", performance));
  }
}

/** Human-readable active filter lines for merged job-list query strings. */
export function buildJobListFilterSummaryLines(
  searchParams: URLSearchParams,
  orderBy?: string | null,
): string[] {
  const lines: string[] = [];
  const endTimeDate = searchParams.get("end_time__date")?.trim();
  if (endTimeDate) {
    lines.push(formatEndTimeDateLine(endTimeDate));
  }

  for (const name of EXTENDED_SEARCH_ALLOWED_PARAM_NAMES) {
    if (HANDLED_IN_EXTENDED_LOOP.has(name)) continue;
    const raw = searchParams.get(name);
    if (!raw || !String(raw).trim()) continue;
    const def = getExtendedSearchParameterDefinition(name);
    if (!def) continue;
    let label = def.label;
    if (name === "account__icontains") label = PROJECT_FIELD_LABEL;
    if (name === "end_time__gte") {
      lines.push(`Job ended ${GTE_OPS[name]} ${raw}`);
      continue;
    }
    if (name === "end_time__lte") {
      lines.push(`Job ended ${LTE_OPS[name]} ${raw}`);
      continue;
    }
    const op = GTE_OPS[name] || LTE_OPS[name];
    if (op) {
      lines.push(`${label} ${op} ${raw}`);
    } else {
      lines.push(`${label}: ${raw}`);
    }
  }

  formatHeaderOnlyParams(searchParams, lines);

  for (const [key, value] of searchParams.entries()) {
    if (!key.startsWith("metrics_") || ROUTE_ONLY_KEYS.has(key)) continue;
    if (!value || !String(value).trim()) continue;
    const match = /^metrics_(.+)__(gte|lte)$/.exec(key);
    if (!match) continue;
    lines.push(formatMetricFilterLine(match[1], match[2], value));
  }

  if (orderBy) {
    lines.push(formatJobListSortSummaryLine(orderBy));
  }

  return lines;
}

/** Build active filter lines from path + query, optionally merging server summary lines. */
export function buildJobListActiveFilterLines(
  searchParams: URLSearchParams,
  routeParams: Record<string, string | string[] | undefined>,
  options?: { orderBy?: string | null; serverSummary?: string[] },
): string[] {
  const merged = mergeRouteParamsIntoSearchParams(searchParams, routeParams);
  const clientLines = buildJobListFilterSummaryLines(merged, options?.orderBy);
  const serverSummary = options?.serverSummary ?? [];
  if (!serverSummary.length) return clientLines;

  const seen = new Set(clientLines);
  const mergedLines = [...clientLines];
  for (const line of serverSummary) {
    if (!seen.has(line)) {
      mergedLines.push(line);
      seen.add(line);
    }
  }
  return mergedLines;
}

export function hasExtendedSearchFilters(searchParams: URLSearchParams): boolean {
  return buildJobListFilterSummaryLines(searchParams).length > 0;
}

export function isExtendedSearchJobsRoute(pathname: string): boolean {
  const base = pathname.replace(/\/$/, "");
  return base === "/jobs" || base.endsWith("/jobs");
}
