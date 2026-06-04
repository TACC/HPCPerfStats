import {
  EXTENDED_SEARCH_ALLOWED_PARAM_NAMES,
  getExtendedSearchParameterDefinition,
} from "./extended-search-parameters";
import { getJobMetricShortLabel } from "./jobMetricDisplayLabels";
import { PROJECT_FIELD_LABEL } from "./site-field-labels";

const GTE_OPS = {
  runtime__gte: "≥",
  nhosts__gte: "≥",
  node_hrs__gte: "≥",
  end_time__gte: "on or after",
};

const LTE_OPS = {
  runtime__lte: "≤",
  nhosts__lte: "≤",
  node_hrs__lte: "≤",
  end_time__lte: "on or before",
};

const ROUTE_ONLY_KEYS = new Set(["page", "order_by"]);

function formatMetricFilterLine(metricKey, op, value) {
  const label = getJobMetricShortLabel(metricKey);
  const sym = op === "gte" ? "≥" : "≤";
  return `${label} ${sym} ${value}`;
}

/**
 * Human-readable active filter lines for extended-search /jobs query strings.
 *
 * @param {URLSearchParams} searchParams
 * @returns {string[]}
 */
export function buildJobListFilterSummaryLines(searchParams) {
  const lines = [];
  for (const name of EXTENDED_SEARCH_ALLOWED_PARAM_NAMES) {
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

  for (const [key, value] of searchParams.entries()) {
    if (!key.startsWith("metrics_") || ROUTE_ONLY_KEYS.has(key)) continue;
    if (!value || !String(value).trim()) continue;
    const match = /^metrics_(.+)__(gte|lte)$/.exec(key);
    if (!match) continue;
    lines.push(formatMetricFilterLine(match[1], match[2], value));
  }

  return lines;
}

/**
 * @param {URLSearchParams} searchParams
 * @returns {boolean}
 */
export function hasExtendedSearchFilters(searchParams) {
  return buildJobListFilterSummaryLines(searchParams).length > 0;
}

/**
 * @param {string} pathname — React Router pathname (no basename)
 * @returns {boolean}
 */
export function isExtendedSearchJobsRoute(pathname) {
  const base = pathname.replace(/\/$/, "");
  return base === "/jobs" || base.endsWith("/jobs");
}
