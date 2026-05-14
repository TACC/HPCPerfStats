/** Human-readable "N mins ago" from unix epoch seconds (for live telemetry). */
export function formatMinsAgo(epochSec) {
  if (epochSec == null || !Number.isFinite(Number(epochSec))) {
    return "—";
  }
  const secs = Math.floor(Date.now() / 1000) - Number(epochSec);
  const mins = Math.max(0, Math.floor(secs / 60));
  if (mins === 1) {
    return "1 min ago";
  }
  return `${mins} mins ago`;
}
