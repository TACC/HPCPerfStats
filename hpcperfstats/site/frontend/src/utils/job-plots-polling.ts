/** Schedule a follow-up job_plots fetch after a server-provided delay (loading / partial). */
export function scheduleJobPlotsRetry(
  fetchFn: () => void,
  retryAfterSeconds: unknown,
  isCancelled: () => boolean,
): void {
  const retryAfterMs = Math.max(250, Number(retryAfterSeconds ?? 2) * 1000);
  setTimeout(() => {
    if (isCancelled()) return;
    fetchFn();
  }, retryAfterMs);
}
