/**
 * Format numeric values for display without scientific notation (decimal / standard only).
 */
export function formatDecimalStandard(value) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return String(value);
  }
  return new Intl.NumberFormat("en-US", {
    notation: "standard",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}
