/** Purple (1 job) → red (100 jobs). Values outside range clamp. */

export const JOB_COUNT_COLOR_MIN = 1;
export const JOB_COUNT_COLOR_MAX = 100;

export function jobCountToHeatColor(jobCount) {
  const n = Math.max(
    JOB_COUNT_COLOR_MIN,
    Math.min(JOB_COUNT_COLOR_MAX, Number(jobCount) || JOB_COUNT_COLOR_MIN),
  );
  const t =
    (n - JOB_COUNT_COLOR_MIN) /
    (JOB_COUNT_COLOR_MAX - JOB_COUNT_COLOR_MIN);
  const hue = 280 * (1 - t);
  return `hsl(${hue}, 72%, 42%)`;
}
