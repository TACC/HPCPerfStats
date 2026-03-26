/**
 * Format an ISO date string for human-readable display.
 * Returns empty string if value is null/undefined/empty; returns original string if invalid date.
 */
export function formatDateTime(isoString) {
  if (isoString == null || isoString === "") return "";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return String(isoString);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}
