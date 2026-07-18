import { stripPresentationParams } from "./filter-identity-params";

/** Merge query-string params with JobList route params (path segments). */
export function buildJobListApiParams(
  searchParams: URLSearchParams,
  routeParams: Record<string, string | string[] | undefined>,
): Record<string, string> {
  const params: Record<string, string> = {
    ...Object.fromEntries(searchParams.entries()),
  };
  const pick = (key: string) => {
    const value = routeParams[key];
    return typeof value === "string" ? value : undefined;
  };
  const year = pick("year");
  const date = pick("date");
  const username = pick("username");
  const account = pick("account");
  const queue = pick("queue");
  const host = pick("host");
  if (year) params.end_time__date = year;
  if (date) params.end_time__date = date;
  if (username) params.username = username;
  if (account) params.account = account;
  if (queue) params.queue = queue;
  if (host) params.host = host;
  return params;
}

/**
 * Filter-identity params for histogram batch — omit page/order_by/tab chrome
 * so sort/pagination does not refetch or clear distributions.
 */
export function buildJobListHistogramApiParams(
  searchParams: URLSearchParams,
  routeParams: Record<string, string | string[] | undefined>,
): Record<string, string> {
  return stripPresentationParams(buildJobListApiParams(searchParams, routeParams));
}
