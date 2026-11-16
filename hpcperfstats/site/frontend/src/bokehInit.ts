/**
 * Load Bokeh from the npm-synced UMD vendor (``loadBokehRuntime`` in ``./bokehjs-bundle``)
 * so the version matches ``@bokeh/bokehjs`` in package.json without Turbopack rewriting it.
 * Keep window.Bokeh for BokehEmbed and tests that stub the global.
 */
import { applyBokehResizeObserverDeferral } from "./patch-resize-observer-for-bokeh";
import type { HpcperfstatsBokehRuntime } from "./bokehjs-bundle";

let bokehLoadPromise: Promise<HpcperfstatsBokehRuntime> | null = null;

export function ensureBokehLoaded(): Promise<HpcperfstatsBokehRuntime | null> {
  if (typeof window === "undefined") {
    return Promise.resolve(null);
  }
  applyBokehResizeObserverDeferral();
  if (window.Bokeh?.embed?.embed_item) {
    return Promise.resolve(window.Bokeh as HpcperfstatsBokehRuntime);
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
