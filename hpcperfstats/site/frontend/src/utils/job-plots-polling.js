/**
 * Schedule a follow-up job_plots fetch after a server-provided delay (loading / partial).
 *
 * @param {() => void} fetchFn
 * @param {unknown} retryAfterSeconds - from API `retry_after_seconds`
 * @param {() => boolean} isCancelled - return true when the owning effect has torn down
 */
export function scheduleJobPlotsRetry(fetchFn, retryAfterSeconds, isCancelled) {
  const retryAfterMs = Math.max(250, Number(retryAfterSeconds ?? 2) * 1000);
  setTimeout(() => {
    if (isCancelled()) return;
    fetchFn();
  }, retryAfterMs);
}
