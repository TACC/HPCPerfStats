/**
 * Normalize queue or metric histogram API payloads for the job list sidecar.
 *
 * @param {object|null|undefined} entry
 * @param {string} [fallbackTitle] - metric name when API omits title (metric group)
 * @returns {{ title: string, plot_item_thumb: unknown, plot_item_full: unknown, plot_unavailable_reason: string|null }|null}
 */
function addRootId(ids, raw) {
  if (typeof raw === "string" && raw.trim().length > 0) {
    ids.add(raw.trim());
    return;
  }
  if (typeof raw === "number" && Number.isFinite(raw)) {
    ids.add(String(raw));
  }
}

function collectBokehDocRootIds(doc) {
  const ids = new Set();
  const roots = doc?.roots;
  if (Array.isArray(roots)) {
    roots.forEach((root) => {
      if (typeof root === "string") {
        addRootId(ids, root);
        return;
      }
      addRootId(ids, root?.id);
    });
  } else if (roots && typeof roots === "object") {
    if (Array.isArray(roots.root_ids)) {
      roots.root_ids.forEach((id) => addRootId(ids, id));
    }
    if (Array.isArray(roots.references)) {
      roots.references.forEach((ref) => {
        addRootId(ids, ref?.id);
      });
    }
  }
  return ids;
}

function hasDeclaredRootInDoc(value) {
  const docRootIds = collectBokehDocRootIds(value.doc);
  if (docRootIds.size === 0) return false;
  const rid = value.root_id;
  if (typeof rid === "string" && rid.trim().length > 0) {
    return docRootIds.has(rid.trim());
  }
  if (typeof rid === "number" && Number.isFinite(rid)) {
    return docRootIds.has(String(rid));
  }
  if (!Array.isArray(value.root_ids) || value.root_ids.length === 0) return false;
  return value.root_ids.every((id) => {
    const key =
      typeof id === "string"
        ? id.trim()
        : typeof id === "number" && Number.isFinite(id)
          ? String(id)
          : "";
    return key.length > 0 && docRootIds.has(key);
  });
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
