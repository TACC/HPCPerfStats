/**
 * Capture Job Detail print-scoped Bokeh canvases as PNG data URLs for React-owned
 * ``<img>`` rendering before ``window.print`` (avoids print-dialog canvas OOM and
 * React wiping imperative DOM). Native canvas only — no html2canvas / jspdf.
 */

type BokehIndexedView = {
  el?: Element;
  remove?: () => void;
};

/** Embed target id prefixes used by Job Detail PlotPanels (suffix ``-${pk}``). */
export const JOB_DETAIL_PRINT_BOKEH_ID_PREFIXES = [
  "job-mscript",
  "job-roofline",
  "job-gpu-roofline",
  "job-multiprecision-cpu",
  "job-multiprecision-gpu",
] as const;

/** Tiny PNG used when the environment cannot rasterize (e.g. jsdom). */
export const PRINT_SNAPSHOT_FALLBACK_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

export function jobDetailPrintBokehTargetIds(pk: string): string[] {
  return JOB_DETAIL_PRINT_BOKEH_ID_PREFIXES.map((prefix) => `${prefix}-${pk}`);
}

export function disposeBokehViewsForTarget(targetEl: HTMLElement | null): void {
  if (!targetEl || typeof window === "undefined" || !window.Bokeh?.index) return;
  Object.values(window.Bokeh.index as Record<string, BokehIndexedView>).forEach(
    (view) => {
      try {
        if (!view?.el || !targetEl.contains(view.el)) return;
        if (typeof view.remove === "function") view.remove();
      } catch {
        // Best-effort teardown.
      }
    },
  );
}

/**
 * Composite visible canvases under ``targetEl`` into a PNG data URL.
 * Does **not** mutate ``targetEl`` (React owns the embed node).
 *
 * @returns data URL, or null when there is nothing to capture.
 */
export function captureBokehTargetDataUrl(targetEl: HTMLElement): string | null {
  const canvases = Array.from(targetEl.querySelectorAll("canvas")).filter(
    (c) => c.width > 0 && c.height > 0,
  );
  if (canvases.length === 0) {
    return null;
  }

  const targetRect = targetEl.getBoundingClientRect();
  let maxRight = 0;
  let maxBottom = 0;
  const layers = canvases.map((canvas) => {
    const r = canvas.getBoundingClientRect();
    const left = r.left - targetRect.left;
    const top = r.top - targetRect.top;
    const width = r.width || canvas.width;
    const height = r.height || canvas.height;
    maxRight = Math.max(maxRight, left + width);
    maxBottom = Math.max(maxBottom, top + height);
    return { canvas, left, top, width, height };
  });

  const cssW = Math.max(1, Math.ceil(maxRight));
  const cssH = Math.max(1, Math.ceil(maxBottom));
  const dpr =
    typeof window !== "undefined" && window.devicePixelRatio > 0
      ? Math.min(window.devicePixelRatio, 2)
      : 1;
  const out = document.createElement("canvas");
  out.width = Math.ceil(cssW * dpr);
  out.height = Math.ceil(cssH * dpr);
  const ctx = out.getContext("2d");
  if (!ctx) {
    return null;
  }
  ctx.scale(dpr, dpr);
  for (const layer of layers) {
    try {
      ctx.drawImage(layer.canvas, layer.left, layer.top, layer.width, layer.height);
    } catch {
      // Skip tainted / detached canvases.
    }
  }

  let dataUrl: string | null = null;
  try {
    dataUrl = out.toDataURL("image/png");
  } catch {
    dataUrl = null;
  }
  if (!dataUrl || !dataUrl.startsWith("data:image/")) {
    return PRINT_SNAPSHOT_FALLBACK_PNG;
  }
  return dataUrl;
}

/** Capture every existing print-scoped Bokeh target for ``pk`` (id → dataUrl). */
export function captureJobDetailPrintBokehSnapshots(pk: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!pk || typeof document === "undefined") return out;
  for (const id of jobDetailPrintBokehTargetIds(pk)) {
    const el = document.getElementById(id);
    if (!el) continue;
    const dataUrl = captureBokehTargetDataUrl(el);
    if (dataUrl) out[id] = dataUrl;
  }
  return out;
}

/** Dispose live Bokeh views and clear embed targets after React has switched to imgs. */
export function disposeJobDetailPrintBokehTargets(pk: string): void {
  if (!pk || typeof document === "undefined") return;
  for (const id of jobDetailPrintBokehTargetIds(pk)) {
    const el = document.getElementById(id);
    if (!el) continue;
    disposeBokehViewsForTarget(el);
    el.innerHTML = "";
  }
}
