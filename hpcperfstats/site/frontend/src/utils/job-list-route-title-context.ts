import type { JobListSelectionContext } from "./job-list-selection-context";

/** Short route context for document titles (job list filters from path + query). */
export function jobListRouteTitleContext(
  selection: JobListSelectionContext,
  searchParams: URLSearchParams,
): string {
  const parts: string[] = [];
  if (selection.year) parts.push(`year ${selection.year}`);
  if (selection.date) parts.push(`date ${selection.date}`);
  if (selection.username) parts.push(`user ${selection.username}`);
  if (selection.account) parts.push(`account ${selection.account}`);
  if (selection.queue) parts.push(`queue ${selection.queue}`);
  if (selection.host) parts.push(`host ${selection.host}`);
  const page = searchParams.get("page");
  if (page && page !== "1") parts.push(`page ${page}`);
  const ob = selection.orderBy || searchParams.get("order_by");
  if (ob) parts.push(`sort ${ob}`);
  return parts.length ? parts.join(" · ") : "";
}

/** One-line human summary of the current job-list slice (for page orientation). */
export function jobListPageHumanSummary(selection: JobListSelectionContext): string | null {
  if (selection.year) {
    return `Jobs that ended during calendar year ${selection.year}.`;
  }
  if (selection.date) {
    return `Jobs with end times matching filter ${selection.date}.`;
  }
  if (selection.username) {
    return `Jobs for user ${selection.username}.`;
  }
  if (selection.account) {
    return `Jobs charged to account ${selection.account}.`;
  }
  if (selection.queue) {
    return `Jobs that ran in queue “${selection.queue}”.`;
  }
  if (selection.host) {
    return `Jobs that ran on host ${selection.host}.`;
  }
  return null;
}
