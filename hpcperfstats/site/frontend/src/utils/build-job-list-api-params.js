/**
 * Merge query-string params with JobList route params (path segments).
 *
 * @param {URLSearchParams} searchParams
 * @param {Record<string, string | undefined>} routeParams - from useParams()
 */
export function buildJobListApiParams(searchParams, routeParams) {
  const params = { ...Object.fromEntries(searchParams.entries()) };
  if (routeParams.year) params.end_time__date = routeParams.year;
  if (routeParams.date) params.end_time__date = routeParams.date;
  if (routeParams.username) params.username = routeParams.username;
  if (routeParams.account) params.account = routeParams.account;
  if (routeParams.queue) params.queue = routeParams.queue;
  if (routeParams.host) params.host = routeParams.host;
  return params;
}
