/**
 * Bokeh json_item payloads use string ids like "p1006". Multiple SPA requests may be
 * served by different app workers that each reset the id counter, so several plots on
 * one page can carry the same ids. BokehJS keeps a global model registry → collisions
 * cause errors such as proxy [[OwnPropertyKeys]] duplicate property reports.
 *
 * Remap every `p` + digits id in the cloned tree consistently so each embed is isolated.
 *
 * @param {object} item - Bokeh json_item (mutates a deep clone only)
 * @returns {object} new plain object safe to pass to embed_item
 */
const BOKEH_NUMERIC_ID = /^p\d+$/;

/** Monotonic block allocator so concurrent embeds never reuse the same id range. */
let nextBokehRemapBase = 200_000_000;

export function prepareBokehJsonItemForEmbed(item) {
  if (!item || typeof item !== "object") {
    return item;
  }
  let clone;
  try {
    clone = JSON.parse(JSON.stringify(item));
  } catch {
    return item;
  }

  const oldIds = new Set();
  function collectIds(node) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach(collectIds);
      return;
    }
    const id = node.id;
    if (typeof id === "string" && BOKEH_NUMERIC_ID.test(id)) {
      oldIds.add(id);
    }
    for (const key of Object.keys(node)) {
      collectIds(node[key]);
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
  const mapping = Object.create(null);
  sorted.forEach((oldId, i) => {
    mapping[oldId] = `p${blockStart + i}`;
  });

  function replaceIds(node) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach(replaceIds);
      return;
    }
    if (typeof node.id === "string" && mapping[node.id]) {
      node.id = mapping[node.id];
    }
    for (const key of Object.keys(node)) {
      replaceIds(node[key]);
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

  replaceIds(clone);
  return clone;
}
