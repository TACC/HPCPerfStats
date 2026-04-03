/**
 * Short route context for document titles (job list filters from path + query).
 *
 * @param {Record<string, string | undefined>} routeParams — from useParams()
 * @param {URLSearchParams} searchParams
 */
export function jobListRouteTitleContext(routeParams, searchParams) {
  const parts = [];
  if (routeParams.year) parts.push(`year ${routeParams.year}`);
  if (routeParams.date) parts.push(`date ${routeParams.date}`);
  if (routeParams.username) parts.push(`user ${routeParams.username}`);
  if (routeParams.account) parts.push(`account ${routeParams.account}`);
  if (routeParams.queue) parts.push(`queue ${routeParams.queue}`);
  if (routeParams.host) parts.push(`host ${routeParams.host}`);
  const page = searchParams.get("page");
  if (page && page !== "1") parts.push(`page ${page}`);
  const ob = searchParams.get("order_by");
  if (ob) parts.push(`sort ${ob}`);
  return parts.length ? parts.join(" · ") : "";
}
