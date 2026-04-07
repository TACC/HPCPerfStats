/**
 * Normalize queue or metric histogram API payloads for the job list sidecar.
 *
 * @param {object|null|undefined} entry
 * @param {string} [fallbackTitle] - metric name when API omits title (metric group)
 * @returns {{ title: string, plot_item_thumb: unknown, plot_item_full: unknown, plot_unavailable_reason: string|null }|null}
 */
function isValidBokehJsonItem(value) {
  if (!value || typeof value !== "object") return false;
  if (!value.doc || typeof value.doc !== "object") return false;
  // Bokeh 2.x json_item used root_ids[]; Bokeh 3.x uses root_id (string).
  if (typeof value.root_id === "string" && value.root_id.trim().length > 0) {
    return true;
  }
  if (!Array.isArray(value.root_ids) || value.root_ids.length === 0) return false;
  return value.root_ids.every((id) => typeof id === "string" && id.trim().length > 0);
}

export function normalizeJobListHistogramEntry(entry, fallbackTitle = "") {
  if (!entry) return null;
  const thumb = isValidBokehJsonItem(entry.plot_item_thumb)
    ? entry.plot_item_thumb
    : null;
  const full = isValidBokehJsonItem(entry.plot_item_full)
    ? entry.plot_item_full
    : null;
  const hasAnyPlot = thumb != null || full != null;
  const payloadInvalid =
    (entry.plot_item_thumb != null && thumb == null) ||
    (entry.plot_item_full != null && full == null);
  const fallbackReason = payloadInvalid
    ? "Histogram payload was invalid; data is unavailable."
    : null;
  return {
    title: entry.title || entry.metric || fallbackTitle || "",
    plot_item_thumb: thumb,
    plot_item_full: full,
    plot_unavailable_reason:
      entry.plot_unavailable_reason || (hasAnyPlot ? null : fallbackReason),
  };
}
