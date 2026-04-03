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

/**
 * One-line human summary of the current job-list slice (for page orientation).
 *
 * @param {Record<string, string | undefined>} routeParams
 * @returns {string|null}
 */
export function jobListPageHumanSummary(routeParams) {
  if (routeParams.year) {
    return `Jobs that ended during calendar year ${routeParams.year}.`;
  }
  if (routeParams.date) {
    return `Jobs with end times matching filter ${routeParams.date}.`;
  }
  if (routeParams.username) {
    return `Jobs for user ${routeParams.username}.`;
  }
  if (routeParams.account) {
    return `Jobs charged to account ${routeParams.account}.`;
  }
  if (routeParams.queue) {
    return `Jobs that ran in queue “${routeParams.queue}”.`;
  }
  if (routeParams.host) {
    return `Jobs that ran on host ${routeParams.host}.`;
  }
  return null;
}
