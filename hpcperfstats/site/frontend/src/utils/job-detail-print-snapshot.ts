/**
 * Capture Job Detail print-scoped Bokeh canvases as PNG data URLs for React-owned
 * ``<img>`` rendering before ``window.print`` (avoids print-dialog canvas OOM and
 * React wiping imperative DOM). Native canvas only — no html2canvas / jspdf.
 *
 * Bokeh 3.x nests ``canvas.bk-layer`` inside open shadow roots
 * (``DOMComponentView.attachShadow`` + ``CanvasView.shadow_el``), so capture must
 * traverse ``shadowRoot`` — plain ``querySelectorAll("canvas")`` finds nothing.
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
 * Collect canvases under ``root``, descending into open shadow roots (Bokeh 3.x).
 * Returns canvases in document order (depth-first, light children after each host's shadow).
 */
export function collectBokehCanvases(root: Element): HTMLCanvasElement[] {
  const out: HTMLCanvasElement[] = [];
  const visit = (node: Element | ShadowRoot): void => {
    for (const child of Array.from(node.children)) {
      if (child instanceof HTMLCanvasElement) {
        out.push(child);
      }
      if (child.shadowRoot) {
        visit(child.shadowRoot);
      }
      visit(child);
    }
  };
  visit(root);
  return out;
}

/**
 * Composite visible canvases under ``targetEl`` into a PNG data URL.
 * Does **not** mutate ``targetEl`` (React owns the embed node).
 *
 * @returns data URL, or null when there is nothing to capture.
 */
export function captureBokehTargetDataUrl(targetEl: HTMLElement): string | null {
  const canvases = collectBokehCanvases(targetEl).filter(
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

/** True when ``targetEl`` has at least one shadow-reachable canvas with a non-zero buffer. */
export function targetHasPrintableBokehCanvases(targetEl: HTMLElement): boolean {
  return collectBokehCanvases(targetEl).some((c) => c.width > 0 && c.height > 0);
}

export type WaitForPrintBokehCanvasesOptions = {
  /** Total poll budget (default 2500 ms). */
  timeoutMs?: number;
  /** Poll interval (default 50 ms). */
  pollMs?: number;
  signal?: AbortSignal;
};

/**
 * Poll until every mounted print-scoped Bokeh target for ``pk`` that still has
 * children has at least one non-zero canvas, or until ``timeoutMs``.
 * Empty / missing targets are skipped (unavailable plots already omitted).
 */
export async function waitForPrintBokehCanvases(
  pk: string,
  options: WaitForPrintBokehCanvasesOptions = {},
): Promise<void> {
  if (!pk || typeof document === "undefined") return;
  const timeoutMs = options.timeoutMs ?? 2500;
  const pollMs = options.pollMs ?? 50;
  const deadline = Date.now() + timeoutMs;

  const pendingTargets = (): boolean => {
    for (const id of jobDetailPrintBokehTargetIds(pk)) {
      const el = document.getElementById(id);
      if (!el || el.children.length === 0) continue;
      if (!targetHasPrintableBokehCanvases(el)) return true;
    }
    return false;
  };

  while (Date.now() < deadline) {
    if (options.signal?.aborted) return;
    if (!pendingTargets()) return;
    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, pollMs);
    });
  }
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

/**
 * Dispose live Bokeh views and clear embed targets that were successfully captured.
 * When ``capturedIds`` is omitted, dispose every print-scoped target for ``pk``.
 * Uncaptured targets keep their live embeds so print can still show the chart.
 */
export function disposeJobDetailPrintBokehTargets(
  pk: string,
  capturedIds?: Iterable<string>,
): void {
  if (!pk || typeof document === "undefined") return;
  const ids =
    capturedIds == null
      ? jobDetailPrintBokehTargetIds(pk)
      : Array.from(capturedIds);
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) continue;
    disposeBokehViewsForTarget(el);
    el.innerHTML = "";
  }
}
