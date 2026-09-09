/** Format elapsed seconds as HH:MM:SS (hours unbounded, zero-padded). */
export function formatRuntimeHms(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return String(value);
  }
  const total = Math.max(0, Math.round(n));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
