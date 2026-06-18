import { JOB_LIST_TABLE_HEADERS } from "./site-field-labels";

const SORT_FIELD_LABELS: Record<string, string> = {
  jid: JOB_LIST_TABLE_HEADERS.jid,
  sample_count: "Sample count",
  performance_sort_rank: JOB_LIST_TABLE_HEADERS.performanceData,
  username: JOB_LIST_TABLE_HEADERS.user,
  account: JOB_LIST_TABLE_HEADERS.project,
  start_time: "Start time",
  end_time: "End time",
  runtime: "Run time",
  queue: "Queue",
  state: "Status",
  ncores: "Cores",
  nhosts: "Nodes",
  node_hrs: "Node hrs",
  jobname: "Name",
};

/** Human-readable sort recap for active filter summary. */
export function formatJobListSortSummaryLine(orderBy: string): string {
  const descending = orderBy.startsWith("-");
  const field = descending ? orderBy.slice(1) : orderBy;
  const label = SORT_FIELD_LABELS[field] || field.replace(/_/g, " ");
  return `Sort: ${label} (${descending ? "descending" : "ascending"})`;
}
