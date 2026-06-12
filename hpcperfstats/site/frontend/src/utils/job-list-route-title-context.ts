/** Short route context for document titles (job list filters from path + query). */
export function jobListRouteTitleContext(
  routeParams: Record<string, string | string[] | undefined>,
  searchParams: URLSearchParams,
): string {
  const parts: string[] = [];
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
  if (year) parts.push(`year ${year}`);
  if (date) parts.push(`date ${date}`);
  if (username) parts.push(`user ${username}`);
  if (account) parts.push(`account ${account}`);
  if (queue) parts.push(`queue ${queue}`);
  if (host) parts.push(`host ${host}`);
  const page = searchParams.get("page");
  if (page && page !== "1") parts.push(`page ${page}`);
  const ob = searchParams.get("order_by");
  if (ob) parts.push(`sort ${ob}`);
  return parts.length ? parts.join(" · ") : "";
}

/** One-line human summary of the current job-list slice (for page orientation). */
export function jobListPageHumanSummary(
  routeParams: Record<string, string | string[] | undefined>,
): string | null {
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
  if (year) {
    return `Jobs that ended during calendar year ${year}.`;
  }
  if (date) {
    return `Jobs with end times matching filter ${date}.`;
  }
  if (username) {
    return `Jobs for user ${username}.`;
  }
  if (account) {
    return `Jobs charged to account ${account}.`;
  }
  if (queue) {
    return `Jobs that ran in queue “${queue}”.`;
  }
  if (host) {
    return `Jobs that ran on host ${host}.`;
  }
  return null;
}
