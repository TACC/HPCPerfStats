import { delayMs } from "./bokeh-embed-defaults";

/**
 * After `Bokeh.embed.embed_item` resolves, nested views may still be finishing layout.
 * Calling `maximizeEmbeddedPlot` (DOM/CSS + `resize`) too early triggers Bokeh 3.9
 * `AxisView` layout while `ranges` are undefined → `can't access property "is_valid", e is undefined`.
 *
 * `Document.is_idle` / `document.idle` fire when the root has notified idle after paint.
 */

export function getBokehDocumentFromEmbedViews(embedResult) {
  if (!embedResult || typeof embedResult !== "object") {
    return null;
  }
  try {
    let list = null;
    const roots = embedResult.roots;
    if (Array.isArray(roots) && roots.length > 0) {
      list = roots;
    } else if (
      (!Array.isArray(roots) || roots.length === 0) &&
      typeof embedResult[Symbol.iterator] === "function"
    ) {
      try {
        list = [...embedResult];
      } catch {
        list = null;
      }
    }
    const first = list?.[0];
    const doc = first?.model?.document ?? null;
    return doc && typeof doc === "object" ? doc : null;
  } catch {
    return null;
  }
}

export function waitForBokehEmbedDocumentIdle(embedResult, options = {}) {
  const timeoutMs = options.timeoutMs ?? 15000;
  const fallbackDelayMs = options.fallbackDelayMs ?? 96;
  const fallbackWhenNoIdleSignalMs = options.fallbackWhenNoIdleSignalMs ?? 120;
  const doc = getBokehDocumentFromEmbedViews(embedResult);
  if (!doc) {
    // No document handle: still give Bokeh a frame to paint (avoids blank canvases with no console error).
    return delayMs(fallbackDelayMs);
  }
  if (typeof doc.is_idle === "boolean" && doc.is_idle) {
    return Promise.resolve();
  }
  if (!doc.idle || typeof doc.idle.connect !== "function") {
    return delayMs(fallbackWhenNoIdleSignalMs);
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
