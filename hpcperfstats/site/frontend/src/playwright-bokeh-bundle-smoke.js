/**
 * Minimal entry used only by bokeh-playwright-smoke.html (Vite multipage build).
 * Playwright loads the built page to assert embed_item works with the same bundled
 * @bokeh/bokehjs chunks as production (not jsDelivr).
 */
import { applyBokehResizeObserverDeferral } from "./patch-resize-observer-for-bokeh.js";
import * as Bokeh from "@bokeh/bokehjs";

applyBokehResizeObserverDeferral();
window.__HPCPERFSTATS_VITE_BOKEH__ = Bokeh;
window.__HPCPERFSTATS_BOKEH_SMOKE_READY__ = true;
