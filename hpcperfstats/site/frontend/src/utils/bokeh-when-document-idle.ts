import { delayMs } from "./bokeh-embed-defaults";

/**
 * After `Bokeh.embed.embed_item` resolves, nested views may still be finishing layout.
 * Calling `maximizeEmbeddedPlot` (DOM/CSS + `resize`) too early triggers Bokeh 3.9
 * `AxisView` layout while `ranges` are undefined → `can't access property "is_valid", e is undefined`.
 *
 * `Document.is_idle` / `document.idle` fire when the root has notified idle after paint.
 */

type BokehIdleDocument = {
  is_idle?: boolean;
  idle?: {
    connect: (slot: () => void) => void;
    disconnect: (slot: () => void) => void;
  };
};

type BokehEmbedView = {
  model?: {
    document?: BokehIdleDocument;
  };
};

export function getBokehDocumentFromEmbedViews(
  embedResult: unknown,
): BokehIdleDocument | null {
  if (!embedResult || typeof embedResult !== "object") {
    return null;
  }
  try {
    let list: BokehEmbedView[] | null = null;
    const roots = (embedResult as { roots?: unknown }).roots;
    if (Array.isArray(roots) && roots.length > 0) {
      list = roots as BokehEmbedView[];
    } else if (
      (!Array.isArray(roots) || roots.length === 0) &&
      typeof (embedResult as Iterable<BokehEmbedView>)[Symbol.iterator] === "function"
    ) {
      try {
        list = [...(embedResult as Iterable<BokehEmbedView>)];
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

export type WaitForBokehEmbedDocumentIdleOptions = {
  timeoutMs?: number;
  fallbackDelayMs?: number;
  fallbackWhenNoIdleSignalMs?: number;
};

export function waitForBokehEmbedDocumentIdle(
  embedResult: unknown,
  options: WaitForBokehEmbedDocumentIdleOptions = {},
): Promise<void> {
  const timeoutMs = options.timeoutMs ?? 15000;
  const fallbackDelayMs = options.fallbackDelayMs ?? 96;
  const fallbackWhenNoIdleSignalMs = options.fallbackWhenNoIdleSignalMs ?? 120;
  const doc = getBokehDocumentFromEmbedViews(embedResult);
  if (!doc) {
    return delayMs(fallbackDelayMs);
  }
  if (typeof doc.is_idle === "boolean" && doc.is_idle) {
    return Promise.resolve();
  }
  if (!doc.idle || typeof doc.idle.connect !== "function") {
    return delayMs(fallbackWhenNoIdleSignalMs);
  }
  const idle = doc.idle;
  return new Promise<void>((resolve) => {
    let settled = false;
    let tid: ReturnType<typeof setTimeout> | null = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (tid != null) {
        clearTimeout(tid);
      }
      try {
        doc.idle?.disconnect(slot);
      } catch {
        // ignore
      }
      resolve();
    };
    function slot() {
      finish();
    }
    idle.connect(slot);
    if (typeof doc.is_idle === "boolean" && doc.is_idle) {
      finish();
      return;
    }
    tid = setTimeout(finish, timeoutMs);
  });
}
