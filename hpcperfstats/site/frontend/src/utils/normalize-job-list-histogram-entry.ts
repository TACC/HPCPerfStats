import type { BokehJsonItem } from "@/types/bokeh";
import type { JobListHistogramEntry } from "@/types/view-models";

type HistogramApiEntry = Record<string, unknown> & {
  title?: string;
  metric?: string;
  plot_item_thumb?: unknown;
  plot_item_full?: unknown;
  plot_unavailable_reason?: string | null;
};

type BokehDocNode = Record<string, unknown> & {
  roots?: unknown;
  root_id?: string | number;
  root_ids?: Array<string | number>;
  doc?: Record<string, unknown> & {
    roots?: unknown;
  };
};

function addRootId(ids: Set<string>, raw: unknown) {
  if (typeof raw === "string" && raw.trim().length > 0) {
    ids.add(raw.trim());
    return;
  }
  if (typeof raw === "number" && Number.isFinite(raw)) {
    ids.add(String(raw));
  }
}

function collectBokehDocRootIds(doc: Record<string, unknown> | undefined) {
  const ids = new Set<string>();
  const roots = doc?.roots;
  if (Array.isArray(roots)) {
    roots.forEach((root) => {
      if (typeof root === "string") {
        addRootId(ids, root);
        return;
      }
      addRootId(ids, (root as { id?: unknown })?.id);
    });
  } else if (roots && typeof roots === "object") {
    const rootsObj = roots as Record<string, unknown>;
    if (Array.isArray(rootsObj.root_ids)) {
      rootsObj.root_ids.forEach((id) => addRootId(ids, id));
    }
    if (Array.isArray(rootsObj.references)) {
      rootsObj.references.forEach((ref) => {
        addRootId(ids, (ref as { id?: unknown })?.id);
      });
    }
  }
  return ids;
}

function hasDeclaredRootInDoc(value: BokehDocNode) {
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

function isValidBokehJsonItem(value: unknown): value is BokehJsonItem {
  if (!value || typeof value !== "object") return false;
  const node = value as BokehDocNode;
  if (!node.doc || typeof node.doc !== "object") return false;
  return hasDeclaredRootInDoc(node);
}

/** Normalize queue or metric histogram API payloads for the job list sidecar. */
export function normalizeJobListHistogramEntry(
  entry: HistogramApiEntry | null | undefined,
  fallbackTitle = "",
): JobListHistogramEntry | null {
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
