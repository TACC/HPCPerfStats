/**
 * Normalize queue or metric histogram API payloads for the job list sidecar.
 *
 * @param {object|null|undefined} entry
 * @param {string} [fallbackTitle] - metric name when API omits title (metric group)
 * @returns {{ title: string, plot_item_thumb: unknown, plot_item_full: unknown, plot_unavailable_reason: string|null }|null}
 */
export function normalizeJobListHistogramEntry(entry, fallbackTitle = "") {
  if (!entry) return null;
  return {
    title: entry.title || entry.metric || fallbackTitle || "",
    plot_item_thumb: entry.plot_item_thumb,
    plot_item_full: entry.plot_item_full,
    plot_unavailable_reason: entry.plot_unavailable_reason || null,
  };
}
