import type { BokehJS } from "@bokeh/bokehjs";

/**
 * Augment Next/Vite ``ImportMetaEnv`` in the global scope.
 * Module-scoped ``interface ImportMetaEnv`` does not merge after Next 16.3
 * ships its own ``ImportMetaEnv``; keep ``VITEST`` here for ``isVitestLike()``.
 */
declare global {
  interface ImportMetaEnv {
    readonly VITEST?: string;
  }

  interface Window {
    Bokeh?: BokehJS;
    /** Set by patch-resize-observer-for-bokeh.ts when deferral is active. */
    __hpcperfstatsResizeObserverDeferred?: boolean;
    __HPCPERFSTATS_BOKEH_SMOKE_READY__?: boolean;
    __HPCPERFSTATS_NEXT_BOKEH__?: typeof import("@/bokehjs-bundle");
  }
}

export {};
