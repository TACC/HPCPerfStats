/** Format an ISO date string for human-readable display. */
export function formatDateTime(isoString: unknown): string {
  if (isoString == null || isoString === "") return "";
  const d = new Date(String(isoString));
  if (Number.isNaN(d.getTime())) return String(isoString);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}
