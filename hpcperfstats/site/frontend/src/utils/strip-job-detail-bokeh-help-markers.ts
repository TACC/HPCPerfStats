import type { BokehJsonItem } from "@/types/bokeh";

/**
 * Job Detail print snapshots must omit the in-plot blue ``?`` help Label from
 * ``add_job_detail_bokeh_help_marker`` (canvas glyph; print CSS cannot hide it).
 * Also drops the invisible screen-unit hit Rect and HoverTools that only target it.
 */

const HELP_HIT_WIDTH = 32;
const HELP_HIT_HEIGHT = 28;

type BokehModelNode = {
  type?: unknown;
  name?: unknown;
  id?: unknown;
  attributes?: Record<string, unknown>;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asModel(node: unknown): BokehModelNode | null {
  if (!isRecord(node)) return null;
  if (node.type !== "object" || typeof node.name !== "string") return null;
  return node as BokehModelNode;
}

function numericAttr(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (isRecord(value) && typeof value.value === "number" && Number.isFinite(value.value)) {
    return value.value;
  }
  return null;
}

function isZeroAlpha(value: unknown): boolean {
  const n = numericAttr(value);
  return n === 0;
}

function isHelpQuestionLabel(node: BokehModelNode): boolean {
  if (node.name !== "Label") return false;
  const text = node.attributes?.text;
  return text === "?";
}

function isHelpHitGlyphRenderer(node: BokehModelNode): boolean {
  if (node.name !== "GlyphRenderer") return false;
  const glyph = asModel(node.attributes?.glyph);
  if (!glyph || glyph.name !== "Rect") return false;
  const ga = glyph.attributes ?? {};
  if (ga.width_units !== "screen" || ga.height_units !== "screen") return false;
  if (numericAttr(ga.width) !== HELP_HIT_WIDTH) return false;
  if (numericAttr(ga.height) !== HELP_HIT_HEIGHT) return false;
  return isZeroAlpha(ga.fill_alpha) && isZeroAlpha(ga.line_alpha);
}

function refId(node: unknown): string | null {
  if (typeof node === "string") return node;
  if (isRecord(node) && typeof node.id === "string") return node.id;
  return null;
}

function collectHelpIds(node: unknown, labelIds: Set<string>, rendererIds: Set<string>): void {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) {
    for (const child of node) collectHelpIds(child, labelIds, rendererIds);
    return;
  }
  const model = asModel(node);
  if (model) {
    const id = typeof model.id === "string" ? model.id : null;
    if (id && isHelpQuestionLabel(model)) labelIds.add(id);
    if (id && isHelpHitGlyphRenderer(model)) rendererIds.add(id);
  }
  for (const value of Object.values(node as Record<string, unknown>)) {
    collectHelpIds(value, labelIds, rendererIds);
  }
}

function hoverTargetsOnlyHelpHits(
  hover: BokehModelNode,
  rendererIds: Set<string>,
): boolean {
  if (hover.name !== "HoverTool") return false;
  const renderers = hover.attributes?.renderers;
  if (!Array.isArray(renderers) || renderers.length === 0) return false;
  return renderers.every((r) => {
    const id = refId(r);
    return id != null && rendererIds.has(id);
  });
}

function shouldDropNode(
  node: unknown,
  labelIds: Set<string>,
  rendererIds: Set<string>,
): boolean {
  const model = asModel(node);
  if (!model) return false;
  const id = typeof model.id === "string" ? model.id : null;
  if (id && (labelIds.has(id) || rendererIds.has(id))) return true;
  if (isHelpQuestionLabel(model)) return true;
  if (isHelpHitGlyphRenderer(model)) return true;
  if (hoverTargetsOnlyHelpHits(model, rendererIds)) return true;
  return false;
}

function stripInPlace(
  node: unknown,
  labelIds: Set<string>,
  rendererIds: Set<string>,
): void {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) {
    for (let i = node.length - 1; i >= 0; i -= 1) {
      if (shouldDropNode(node[i], labelIds, rendererIds)) {
        node.splice(i, 1);
      } else {
        stripInPlace(node[i], labelIds, rendererIds);
      }
    }
    return;
  }
  const record = node as Record<string, unknown>;
  for (const key of Object.keys(record)) {
    const value = record[key];
    if (Array.isArray(value)) {
      for (let i = value.length - 1; i >= 0; i -= 1) {
        if (shouldDropNode(value[i], labelIds, rendererIds)) {
          value.splice(i, 1);
        } else {
          stripInPlace(value[i], labelIds, rendererIds);
        }
      }
    } else if (isRecord(value) && shouldDropNode(value, labelIds, rendererIds)) {
      delete record[key];
    } else {
      stripInPlace(value, labelIds, rendererIds);
    }
  }
}

/**
 * Mutate a cloned ``json_item`` tree in place, removing Job Detail help markers.
 */
export function stripJobDetailBokehHelpMarkersInPlace(root: unknown): void {
  if (!root || typeof root !== "object") return;
  const labelIds = new Set<string>();
  const rendererIds = new Set<string>();
  collectHelpIds(root, labelIds, rendererIds);
  if (labelIds.size === 0 && rendererIds.size === 0) return;
  stripInPlace(root, labelIds, rendererIds);
}

/**
 * Deep-clone ``item`` and strip Job Detail in-plot help markers for print embeds.
 */
export function stripJobDetailBokehHelpMarkers(
  item: BokehJsonItem | null | undefined,
): BokehJsonItem | null | undefined {
  if (!item || typeof item !== "object") return item;
  let clone: BokehJsonItem;
  try {
    clone = JSON.parse(JSON.stringify(item)) as BokehJsonItem;
  } catch {
    return item;
  }
  stripJobDetailBokehHelpMarkersInPlace(clone);
  return clone;
}

/** Test helper: whether a tree still contains a help ``?`` Label. */
export function jsonItemHasHelpQuestionLabel(item: unknown): boolean {
  const labelIds = new Set<string>();
  const rendererIds = new Set<string>();
  collectHelpIds(item, labelIds, rendererIds);
  return labelIds.size > 0;
}

/** Exported for tests — id of a GlyphRenderer recognized as the help hit target. */
export function jsonItemHelpHitRendererIds(item: unknown): string[] {
  const labelIds = new Set<string>();
  const rendererIds = new Set<string>();
  collectHelpIds(item, labelIds, rendererIds);
  return [...rendererIds];
}
