/**
 * Load Bokeh from the npm package so the version matches @bokeh/bokehjs in package.json.
 * Keep window.Bokeh for BokehEmbed and tests that stub the global.
 */
import * as Bokeh from "@bokeh/bokehjs";

if (typeof window !== "undefined") {
  window.Bokeh = Bokeh;
}

export { Bokeh };
