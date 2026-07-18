/**
 * Defense against hollow Orval Zod Bokeh schemas that strip ``type`` / ``attributes``
 * from ``doc.roots[]`` (BokehJS then reports ``reference p… isn't known`` after id remap).
 *
 * Prefer opaque OpenAPI ``JSONField`` → ``zod.unknown()``. This merge restores plot
 * fields from the raw HTTP JSON when the parsed tree is hollow.
 */

const BOKEH_FIELD_KEYS = new Set([
  "plot_item",
  "plot_item_thumb",
  "plot_item_full",
  "mplot_item",
  "rplot_item",
  "grplot_item",
  "tplot_item",
  "multiprecision_cpu_plot_item",
  "multiprecision_gpu_plot_item",
  "bokeh_histogram_json_item",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

/** True when a Bokeh json_item lost model definitions (id-only roots). */
export function isHollowBokehJsonItem(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const doc = value.doc;
  if (!isRecord(doc)) return false;
  const roots = doc.roots;
  if (!Array.isArray(roots) || roots.length === 0) return false;
  return roots.some((root) => {
    if (!isRecord(root)) return false;
    if (root.id == null) return false;
    return root.type == null && root.attributes == null;
  });
}

function restoreBokehField(rawField: unknown, parsedField: unknown): unknown {
  if (rawField == null) return parsedField;
  if (parsedField == null || isHollowBokehJsonItem(parsedField)) {
    return rawField;
  }
  return parsedField;
}

function restoreInObject(
  raw: Record<string, unknown>,
  parsed: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...parsed };
  for (const key of Object.keys(parsed)) {
    if (BOKEH_FIELD_KEYS.has(key)) {
      out[key] = restoreBokehField(raw[key], parsed[key]);
      continue;
    }
    const rawChild = raw[key];
    const parsedChild = parsed[key];
    if (Array.isArray(parsedChild) && Array.isArray(rawChild)) {
      out[key] = parsedChild.map((item, index) => {
        const rawItem = rawChild[index];
        if (isRecord(item) && isRecord(rawItem)) {
          return restoreInObject(rawItem, item);
        }
        return item;
      });
      continue;
    }
    if (isRecord(parsedChild) && isRecord(rawChild)) {
      out[key] = restoreInObject(rawChild, parsedChild);
    }
  }
  return out;
}

/** Re-attach full Bokeh trees from ``raw`` when Zod left hollow stubs in ``parsed``. */
export function restoreStrippedBokehFields(raw: unknown, parsed: unknown): unknown {
  if (!isRecord(raw) || !isRecord(parsed)) return parsed;
  return restoreInObject(raw, parsed);
}
