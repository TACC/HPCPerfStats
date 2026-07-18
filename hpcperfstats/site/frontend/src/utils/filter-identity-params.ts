/** Presentation-only query keys — must not drive secondary refetch or reset enlarge/popovers. */
export const URL_PRESENTATION_PARAM_KEYS = new Set([
  "page",
  "order_by",
  "performance_sort_rank",
  "view",
  "tab",
]);

/** Drop presentation keys from a flat param record (JobList hist / filter identity). */
export function stripPresentationParams(
  params: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(params)) {
    if (URL_PRESENTATION_PARAM_KEYS.has(key)) continue;
    out[key] = value;
  }
  return out;
}

/**
 * Stable filter-identity key from URLSearchParams (sorted, presentation keys omitted).
 * Use for enlarge/popover reset effects — not full `searchParams.toString()`.
 */
export function filterIdentitySearchParamsKey(
  searchParams: URLSearchParams,
): string {
  const pairs: Array<[string, string]> = [];
  searchParams.forEach((value, key) => {
    if (URL_PRESENTATION_PARAM_KEYS.has(key)) return;
    pairs.push([key, value]);
  });
  pairs.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return pairs.map(([key, value]) => `${key}=${value}`).join("&");
}
