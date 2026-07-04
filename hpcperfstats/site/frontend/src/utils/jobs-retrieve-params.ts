import type { JobsRetrieveParams } from "@/api/generated/models/jobsRetrieveParams";
import { EXTENDED_SEARCH_ALLOWED_PARAM_NAMES } from "./extended-search-parameters";

const JOB_LIST_PAGINATION_KEYS = new Set(["page", "order_by", "performance_sort_rank"]);
const JOB_LIST_EXTRA_KEYS = new Set(["include_filter_options", "host", "jid"]);

const METRIC_FILTER_KEY = /^metrics_[a-zA-Z0-9_.-]+__(?:gte|lte)$/;

function isAllowedJobListParam(key: string): boolean {
  if (JOB_LIST_PAGINATION_KEYS.has(key)) return true;
  if (JOB_LIST_EXTRA_KEYS.has(key)) return true;
  if (EXTENDED_SEARCH_ALLOWED_PARAM_NAMES.includes(key)) return true;
  return METRIC_FILTER_KEY.test(key);
}

/** Strip unknown query keys before Orval jobs retrieve (aligns with Django query_utils filters). */
export function buildJobsRetrieveParams(
  searchParams: Record<string, string>,
): JobsRetrieveParams & Record<string, string | number | undefined> {
  const out: Record<string, string | number | undefined> = {};
  for (const [key, raw] of Object.entries(searchParams)) {
    if (!isAllowedJobListParam(key)) continue;
    const value = String(raw ?? "").trim();
    if (!value) continue;
    if (key === "page") {
      const page = Number(value);
      if (Number.isFinite(page) && page > 0) out.page = page;
      continue;
    }
    if (key === "include_filter_options") {
      out.include_filter_options = value === "0" ? 0 : 1;
      continue;
    }
    out[key] = value;
  }
  if (out.include_filter_options == null) {
    out.include_filter_options = 0;
  }
  return out as JobsRetrieveParams & Record<string, string | number | undefined>;
}
