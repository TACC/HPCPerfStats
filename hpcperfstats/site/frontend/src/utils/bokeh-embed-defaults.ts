/**
 * Defaults for Bokeh SPA embed (viewport gating + post-idle stagger).
 * Centralizes Vitest vs production behavior.
 */

export function isVitestLike() {
  return typeof import.meta !== "undefined" && !!import.meta.env?.VITEST;
}

/** When unset on BokehEmbed, defer embed until wrapper intersects viewport (production only). */
export function defaultDeferEmbedUntilVisible() {
  return !isVitestLike();
}

/**
 * After Document.idle, wait this long before releasing the global embed lock (ms).
 * Reduces overlapping ResizeObserver work across concurrent Bokeh documents.
 */
export function defaultEmbedSettleAfterIdleMs() {
  return isVitestLike() ? 0 : 24;
}

export const DEFAULT_INTERSECTION_ROOT_MARGIN = "100px 0px";

export const DEFAULT_INTERSECTION_THRESHOLD = 0.01;

export function delayMs(ms: number): Promise<void> {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    setTimeout(resolve, n);
  });
}
