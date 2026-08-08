import type { BokehJsonItem } from "@/types/bokeh";
import { stripJobDetailBokehHelpMarkersInPlace } from "./strip-job-detail-bokeh-help-markers";

/**
 * Bokeh json_item payloads use string ids like "p1006". Multiple SPA requests may be
 * served by different app workers that each reset the id counter, so several plots on
 * one page can carry the same ids. BokehJS keeps a global model registry → collisions
 * cause errors such as proxy [[OwnPropertyKeys]] duplicate property reports.
 *
 * Remap every `p` + digits id in the cloned tree consistently so each embed is isolated.
 */
const BOKEH_NUMERIC_ID = /^p\d+$/;

/** Monotonic block allocator so concurrent embeds never reuse the same id range. */
let nextBokehRemapBase = 200_000_000;

type JsonTree = Record<string, unknown> | unknown[];

export type PrepareBokehJsonItemForEmbedOptions = {
  /** Print prep: drop in-plot ``?`` help Labels / hit HoverTools before embed. */
  stripHelpMarkers?: boolean;
};

export function prepareBokehJsonItemForEmbed(
  item: BokehJsonItem | null | undefined,
  options?: PrepareBokehJsonItemForEmbedOptions,
): BokehJsonItem | null | undefined {
  if (!item || typeof item !== "object") {
    return item;
  }
  let clone: BokehJsonItem;
  try {
    clone = JSON.parse(JSON.stringify(item)) as BokehJsonItem;
  } catch {
    return item;
  }

  if (options?.stripHelpMarkers) {
    stripJobDetailBokehHelpMarkersInPlace(clone);
  }

  const oldIds = new Set<string>();
  function collectIds(node: unknown) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach(collectIds);
      return;
    }
    const record = node as Record<string, unknown>;
    const id = record.id;
    if (typeof id === "string" && BOKEH_NUMERIC_ID.test(id)) {
      oldIds.add(id);
    }
    for (const key of Object.keys(record)) {
      collectIds(record[key]);
    }
  }
  collectIds(clone);
  if (typeof clone.root_id === "string" && BOKEH_NUMERIC_ID.test(clone.root_id)) {
    oldIds.add(clone.root_id);
  }
  if (Array.isArray(clone.root_ids)) {
    for (const rid of clone.root_ids) {
      if (typeof rid === "string" && BOKEH_NUMERIC_ID.test(rid)) {
        oldIds.add(rid);
      }
    }
  }

  if (oldIds.size === 0) {
    return clone;
  }

  const sorted = [...oldIds].sort();
  const blockStart = nextBokehRemapBase;
  nextBokehRemapBase += sorted.length + 64;
  const mapping: Record<string, string> = Object.create(null);
  sorted.forEach((oldId, i) => {
    mapping[oldId] = `p${blockStart + i}`;
  });

  function replaceIds(node: unknown) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach(replaceIds);
      return;
    }
    const record = node as Record<string, unknown>;
    if (typeof record.id === "string" && mapping[record.id]) {
      record.id = mapping[record.id];
    }
    for (const key of Object.keys(record)) {
      replaceIds(record[key]);
    }
  }

  if (typeof clone.root_id === "string" && mapping[clone.root_id]) {
    clone.root_id = mapping[clone.root_id];
  }
  if (Array.isArray(clone.root_ids)) {
    clone.root_ids = clone.root_ids.map((rid) =>
      typeof rid === "string" && mapping[rid] ? mapping[rid] : rid,
    );
  }

  replaceIds(clone as JsonTree);
  return clone;
}
