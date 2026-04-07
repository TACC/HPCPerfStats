/**
 * Load Bokeh from the npm package so the version matches @bokeh/bokehjs in package.json.
 * Dynamic import keeps @bokeh/bokehjs in its own async chunk (large library).
 * Keep window.Bokeh for BokehEmbed and tests that stub the global.
 */
let bokehLoadPromise = null;

export function ensureBokehLoaded() {
  if (typeof window === "undefined") {
    return Promise.resolve(null);
  }
  if (window.Bokeh) {
    return Promise.resolve(window.Bokeh);
  }
  if (!bokehLoadPromise) {
    bokehLoadPromise = import("@bokeh/bokehjs").then((mod) => {
      window.Bokeh = mod;
      return mod;
    });
  }
  return bokehLoadPromise;
}
