/** URL helpers for job list header multi-select filters (comma-separated query values). */

import { hrefFromPathAndSearch, replacePathIfChanged } from "./replace-path-if-changed";

export const JOB_LIST_HEADER_FILTER_KEYS = [
  "username",
  "account",
  "queue",
  "state",
  "performance_sort_rank",
] as const;

export type JobListHeaderFilterKey = (typeof JOB_LIST_HEADER_FILTER_KEYS)[number];

/** Time-window query keys that header filter navigation must never drop. */
const JOB_LIST_TIME_SELECTION_KEYS = [
  "end_time__date",
  "end_time__date__gte",
  "end_time__date__lte",
  "end_time__gte",
  "end_time__lte",
  "start_time__date",
  "start_time__date__gte",
  "start_time__date__lte",
  "start_time__gte",
  "start_time__lte",
] as const;

const ROUTE_FILTER_KEYS = ["year", "date", "username", "account", "queue", "host"] as const;

const ROUTE_KEY_FOR_FILTER: Partial<Record<JobListHeaderFilterKey, string>> = {
  username: "username",
  account: "account",
  queue: "queue",
};

/** Merge browse-route path segments into query params for display and API parity. */
export function mergeRouteParamsIntoSearchParams(
  searchParams: URLSearchParams,
  routeParams: Record<string, string | string[] | undefined>,
): URLSearchParams {
  const next = new URLSearchParams(searchParams.toString());
  const pick = (key: string) => {
    const value = routeParams[key];
    return typeof value === "string" ? value : undefined;
  };
  const year = pick("year");
  const date = pick("date");
  if (year) next.set("end_time__date", year);
  else if (date) next.set("end_time__date", date);
  for (const key of ["username", "account", "queue", "host"] as const) {
    const value = pick(key);
    if (value) next.set(key, value);
  }
  return next;
}

function snapshotTimeSelectionParams(params: URLSearchParams): Map<string, string> {
  const out = new Map<string, string>();
  for (const key of JOB_LIST_TIME_SELECTION_KEYS) {
    const value = params.get(key);
    if (value) out.set(key, value);
  }
  return out;
}

function applyTimeSnapshot(snapshot: Map<string, string>, target: URLSearchParams): void {
  for (const [key, value] of snapshot) {
    target.set(key, value);
  }
}

function pathnameUsesRouteFilters(pathname: string): boolean {
  return ROUTE_FILTER_KEYS.some((key) => {
    if (key === "year") return pathname.startsWith("/machine/year/");
    if (key === "date") return pathname.startsWith("/machine/date/");
    if (key === "username") return pathname.startsWith("/machine/username/");
    if (key === "account") return pathname.startsWith("/machine/account/");
    if (key === "queue") return pathname.startsWith("/machine/queue/");
    if (key === "host") return pathname.startsWith("/machine/host/");
    return false;
  });
}

export function parseHeaderFilterSet(
  searchParams: URLSearchParams,
  key: JobListHeaderFilterKey,
): Set<string> {
  const raw = searchParams.get(key);
  if (!raw) return new Set();
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const token = part.trim();
    if (!token || seen.has(token)) continue;
    seen.add(token);
    out.push(token);
  }
  return new Set(out);
}

/** Merge URL param set with browse-route segment value when query param is absent. */
export function readHeaderFilterSet(
  searchParams: URLSearchParams,
  routeParams: Record<string, string | string[] | undefined>,
  key: JobListHeaderFilterKey,
): Set<string> {
  const fromQuery = parseHeaderFilterSet(searchParams, key);
  if (fromQuery.size > 0) return fromQuery;
  const routeKey = ROUTE_KEY_FOR_FILTER[key];
  if (!routeKey) return fromQuery;
  const routeVal = routeParams[routeKey];
  if (typeof routeVal === "string" && routeVal.trim()) {
    return new Set([routeVal.trim()]);
  }
  return fromQuery;
}

export function toggleHeaderFilterValue(current: Set<string>, value: string): Set<string> {
  const next = new Set(current);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export function serializeHeaderFilterSet(values: Set<string>): string | null {
  if (!values.size) return null;
  return [...values].join(",");
}

export type ApplyHeaderFilterChangeArgs = {
  router: { replace: (href: string) => void };
  pathname: string;
  searchParams: URLSearchParams;
  routeParams: Record<string, string | string[] | undefined>;
  key: JobListHeaderFilterKey;
  nextValues: Set<string>;
};

export type HeaderFilterHrefArgs = Omit<ApplyHeaderFilterChangeArgs, "key" | "nextValues"> & {
  mutate?: (params: URLSearchParams) => void;
};

/** Build target pathname + params for a header filter navigation (exported for tests). */
export function buildHeaderFilterHref({
  pathname,
  searchParams,
  routeParams,
  mutate,
}: HeaderFilterHrefArgs): { targetPath: string; params: URLSearchParams } {
  const params = mergeRouteParamsIntoSearchParams(searchParams, routeParams);
  const timeSnapshot = snapshotTimeSelectionParams(params);
  mutate?.(params);
  applyTimeSnapshot(timeSnapshot, params);

  let targetPath = pathname;
  if (pathnameUsesRouteFilters(pathname)) {
    targetPath = "/machine/jobs/";
  }

  return { targetPath, params };
}

function navigateHeaderFilterChange(
  args: HeaderFilterHrefArgs,
): void {
  const { targetPath, params } = buildHeaderFilterHref(args);
  replacePathIfChanged(
    args.router,
    targetPath,
    params,
    args.pathname,
    args.searchParams,
  );
}

/** Update one header filter dimension in the URL; reset page; normalize browse routes to /machine/jobs/. */
export function applyHeaderFilterChange({
  router,
  pathname,
  searchParams,
  routeParams,
  key,
  nextValues,
}: ApplyHeaderFilterChangeArgs): void {
  navigateHeaderFilterChange({
    router,
    pathname,
    searchParams,
    routeParams,
    mutate: (params) => {
      const serialized = serializeHeaderFilterSet(nextValues);
      if (serialized) {
        params.set(key, serialized);
      } else {
        params.delete(key);
      }
      params.delete("page");
    },
  });
}

export function clearAllHeaderFilters({
  router,
  pathname,
  searchParams,
  routeParams,
}: Omit<ApplyHeaderFilterChangeArgs, "key" | "nextValues">): void {
  navigateHeaderFilterChange({
    router,
    pathname,
    searchParams,
    routeParams,
    mutate: (params) => {
      for (const key of JOB_LIST_HEADER_FILTER_KEYS) {
        params.delete(key);
      }
      params.delete("page");
    },
  });
}

export { hrefFromPathAndSearch };

export function clearHeaderFilterDimension(
  args: Omit<ApplyHeaderFilterChangeArgs, "nextValues">,
): void {
  applyHeaderFilterChange({ ...args, nextValues: new Set() });
}
