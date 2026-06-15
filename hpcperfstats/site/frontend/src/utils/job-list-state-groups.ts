/** Major terminal job-state group keys for job list header filters (mirrors backend). */
export const MAJOR_JOB_STATE_LABELS: Record<string, string> = {
  completed: "Completed",
  failed: "Failed",
  canceled: "Canceled",
  preempted: "Preempted",
};

export function majorJobStateLabel(key: string): string {
  return MAJOR_JOB_STATE_LABELS[key] ?? key;
}
