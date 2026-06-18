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

/** Parallel Bokeh embed pipelines (shard by embed id to reduce head-of-line blocking). */
export const BOKEH_EMBED_LOCK_SHARDS = 4;

export function bokehEmbedLockShard(embedId: string): number {
  const id = embedId || "default";
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash + id.charCodeAt(i)) % BOKEH_EMBED_LOCK_SHARDS;
  }
  return hash;
}

export const DEFAULT_INTERSECTION_ROOT_MARGIN = "100px 0px";

export const DEFAULT_INTERSECTION_THRESHOLD = 0.01;

/** Delay between concurrent list/dashboard thumbnail embed starts (ms). */
export const LIST_EMBED_STAGGER_MS = 200;

export function delayMs(ms: number): Promise<void> {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    setTimeout(resolve, n);
  });
}
