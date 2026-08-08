/**
 * Rasterize Job Detail print-scoped Bokeh canvases to static images and dispose
 * live Bokeh views before ``window.print`` (avoids print-dialog canvas memory blowups).
 * Native canvas only — no html2canvas / jspdf.
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
 * Composite visible canvases under ``targetEl`` into one PNG ``<img>``, dispose
 * Bokeh views, and replace target children with that image.
 *
 * @returns true when an image was written; false when there was nothing to snapshot.
 */
export function snapshotBokehTargetToStaticImage(targetEl: HTMLElement): boolean {
  const canvases = Array.from(targetEl.querySelectorAll("canvas")).filter(
    (c) => c.width > 0 && c.height > 0,
  );
  if (canvases.length === 0) {
    disposeBokehViewsForTarget(targetEl);
    const bkRoot = targetEl.querySelector(".bk-root");
    if (bkRoot) {
      targetEl.innerHTML = "";
    }
    return false;
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
    disposeBokehViewsForTarget(targetEl);
    return false;
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
  // jsdom lacks canvas rasterization; fall back to a tiny valid PNG so print still
  // disposes live Bokeh views (production browsers return a real data URL).
  if (!dataUrl || !dataUrl.startsWith("data:image/")) {
    dataUrl =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
  }

  disposeBokehViewsForTarget(targetEl);
  targetEl.innerHTML = "";
  const img = document.createElement("img");
  img.src = dataUrl;
  img.alt = "Plot snapshot for print";
  img.className = "job-detail-print-plot-snapshot w-full h-auto max-w-full";
  img.width = cssW;
  img.height = cssH;
  targetEl.appendChild(img);
  return true;
}

/** Snapshot every Job Detail print-scoped Bokeh embed target for ``pk``. */
export function snapshotJobDetailPrintBokehTargets(pk: string): void {
  if (!pk || typeof document === "undefined") return;
  for (const id of jobDetailPrintBokehTargetIds(pk)) {
    const el = document.getElementById(id);
    if (el) snapshotBokehTargetToStaticImage(el);
  }
}
