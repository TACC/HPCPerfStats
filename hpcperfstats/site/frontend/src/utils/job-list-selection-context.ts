import { mergeRouteParamsIntoSearchParams } from "./job-list-header-filter-params";

export type JobListSelectionContext = {
  year?: string;
  date?: string;
  username?: string;
  account?: string;
  queue?: string;
  host?: string;
  endTimeDate?: string;
  orderBy?: string;
};

function pickRouteParam(
  routeParams: Record<string, string | string[] | undefined>,
  key: string,
): string | undefined {
  const value = routeParams[key];
  return typeof value === "string" ? value : undefined;
}

function isYearToken(value: string): boolean {
  return value.length === 4 && /^\d{4}$/.test(value);
}

/** Merged browse + query selection used for titles, breadcrumbs, and active filters. */
export function resolveJobListSelectionContext(
  searchParams: URLSearchParams,
  routeParams: Record<string, string | string[] | undefined>,
): JobListSelectionContext {
  const merged = mergeRouteParamsIntoSearchParams(searchParams, routeParams);
  const endTimeDate = merged.get("end_time__date")?.trim() || undefined;
  const year =
    pickRouteParam(routeParams, "year") ||
    (endTimeDate && isYearToken(endTimeDate) ? endTimeDate : undefined);
  const date =
    pickRouteParam(routeParams, "date") ||
    (endTimeDate && !isYearToken(endTimeDate) ? endTimeDate : undefined);

  return {
    year,
    date,
    username: pickRouteParam(routeParams, "username") || merged.get("username") || undefined,
    account: pickRouteParam(routeParams, "account") || merged.get("account") || undefined,
    queue: pickRouteParam(routeParams, "queue") || merged.get("queue") || undefined,
    host: pickRouteParam(routeParams, "host") || merged.get("host") || undefined,
    endTimeDate,
    orderBy: searchParams.get("order_by")?.trim() || undefined,
  };
}
