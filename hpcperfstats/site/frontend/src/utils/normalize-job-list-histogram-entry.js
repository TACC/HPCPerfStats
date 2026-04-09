/**
 * Normalize queue or metric histogram API payloads for the job list sidecar.
 *
 * @param {object|null|undefined} entry
 * @param {string} [fallbackTitle] - metric name when API omits title (metric group)
 * @returns {{ title: string, plot_item_thumb: unknown, plot_item_full: unknown, plot_unavailable_reason: string|null }|null}
 */
function collectBokehDocRootIds(doc) {
  const ids = new Set();
  const roots = doc?.roots;
  if (Array.isArray(roots)) {
    roots.forEach((root) => {
      const id = root?.id;
      if (typeof id === "string" && id.trim().length > 0) ids.add(id.trim());
    });
  } else if (roots && typeof roots === "object") {
    if (Array.isArray(roots.root_ids)) {
      roots.root_ids.forEach((id) => {
        if (typeof id === "string" && id.trim().length > 0) ids.add(id.trim());
      });
    }
    if (Array.isArray(roots.references)) {
      roots.references.forEach((ref) => {
        const id = ref?.id;
        if (typeof id === "string" && id.trim().length > 0) ids.add(id.trim());
      });
    }
  }
  return ids;
}

function hasDeclaredRootInDoc(value) {
  const docRootIds = collectBokehDocRootIds(value.doc);
  if (docRootIds.size === 0) return false;
  if (typeof value.root_id === "string" && value.root_id.trim().length > 0) {
    return docRootIds.has(value.root_id.trim());
  }
  if (!Array.isArray(value.root_ids) || value.root_ids.length === 0) return false;
  return value.root_ids.every(
    (id) => typeof id === "string" && id.trim().length > 0 && docRootIds.has(id.trim()),
  );
}

function isValidBokehJsonItem(value) {
  if (!value || typeof value !== "object") return false;
  if (!value.doc || typeof value.doc !== "object") return false;
  // Accept both Bokeh 2.x/3.x shapes, but require roots declared in doc payload.
  return hasDeclaredRootInDoc(value);
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
