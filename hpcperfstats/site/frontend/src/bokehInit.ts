/**
 * Load Bokeh from the npm package so the version matches @bokeh/bokehjs in package.json.
 * Dynamic import keeps BokehJS in its own async chunk (large library).
 * Keep window.Bokeh for BokehEmbed and tests that stub the global.
 *
 * Import via ``./bokehjs-bundle`` (not package ``main``) so Turbopack can resolve
 * Bokeh 3.10+ lib modules — see ``bokehjs-bundle.ts``.
 */
import { applyBokehResizeObserverDeferral } from "./patch-resize-observer-for-bokeh";

let bokehLoadPromise: Promise<typeof import("./bokehjs-bundle")> | null = null;

export function ensureBokehLoaded() {
  if (typeof window === "undefined") {
    return Promise.resolve(null);
  }
  applyBokehResizeObserverDeferral();
  if (window.Bokeh) {
    return Promise.resolve(window.Bokeh);
  }
  if (!bokehLoadPromise) {
    bokehLoadPromise = import("./bokehjs-bundle").then((mod) => {
      window.Bokeh = mod;
      return mod;
    });
  }
  return bokehLoadPromise;
}
