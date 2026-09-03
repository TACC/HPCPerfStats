/**
 * Load Bokeh from the npm-synced UMD vendor so the version matches
 * ``@bokeh/bokehjs`` in package.json without Turbopack rewriting the library.
 * Keep window.Bokeh for BokehEmbed and tests that stub the global.
 *
 * Import via ``./bokehjs-bundle`` (``loadBokehRuntime`` → ``/static/frontend/vendor/bokeh.min.js``)
 * — see ``bokehjs-bundle.ts`` and ``scripts/sync-bokeh-vendor.mjs``.
 */
import { applyBokehResizeObserverDeferral } from "./patch-resize-observer-for-bokeh";
import type { HpcperfstatsBokehRuntime } from "./bokehjs-bundle";

type BokehRuntime = HpcperfstatsBokehRuntime;

let bokehLoadPromise: Promise<BokehRuntime> | null = null;

export function ensureBokehLoaded(): Promise<BokehRuntime | null> {
  if (typeof window === "undefined") {
    return Promise.resolve(null);
  }
  applyBokehResizeObserverDeferral();
  if (window.Bokeh?.embed?.embed_item) {
    return Promise.resolve(window.Bokeh as BokehRuntime);
  }
  if (!bokehLoadPromise) {
    bokehLoadPromise = import("./bokehjs-bundle").then((mod) =>
      mod.loadBokehRuntime().then((Bokeh) => {
        window.Bokeh = Bokeh;
        return Bokeh;
      }),
    );
  }
  return bokehLoadPromise;
}
