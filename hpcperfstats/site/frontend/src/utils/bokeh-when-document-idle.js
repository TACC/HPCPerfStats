/**
 * After `Bokeh.embed.embed_item` resolves, nested views may still be finishing layout.
 * Calling `maximizeEmbeddedPlot` (DOM/CSS + `resize`) too early triggers Bokeh 3.9
 * `AxisView` layout while `ranges` are undefined → `can't access property "is_valid", e is undefined`.
 *
 * `Document.is_idle` / `document.idle` fire when the root has notified idle after paint.
 *
 * @param {unknown} embedResult - return value of `embed_item` (typically a ViewManager with `.roots`)
 * @param {{ timeoutMs?: number }} [options]
 * @returns {Promise<void>}
 */
export function getBokehDocumentFromEmbedViews(embedResult) {
  if (!embedResult || typeof embedResult !== "object") {
    return null;
  }
  try {
    const roots = embedResult.roots;
    const list = Array.isArray(roots) ? roots : null;
    const first = list?.[0];
    const doc = first?.model?.document ?? null;
    return doc && typeof doc === "object" ? doc : null;
  } catch {
    return null;
  }
}

export function waitForBokehEmbedDocumentIdle(embedResult, options = {}) {
  const timeoutMs = options.timeoutMs ?? 15000;
  const doc = getBokehDocumentFromEmbedViews(embedResult);
  if (!doc) {
    return Promise.resolve();
  }
  if (typeof doc.is_idle === "boolean" && doc.is_idle) {
    return Promise.resolve();
  }
  if (!doc.idle || typeof doc.idle.connect !== "function") {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let settled = false;
    let tid = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (tid != null) {
        clearTimeout(tid);
      }
      try {
        doc.idle.disconnect(slot);
      } catch {
        // ignore
      }
      resolve();
    };
    function slot() {
      finish();
    }
    doc.idle.connect(slot);
    if (typeof doc.is_idle === "boolean" && doc.is_idle) {
      finish();
      return;
    }
    tid = setTimeout(finish, timeoutMs);
  });
}
