"use client";

import { useEffect } from "react";
import { applyBokehResizeObserverDeferral } from "@/patch-resize-observer-for-bokeh";

declare global {
  interface Window {
    __HPCPERFSTATS_NEXT_BOKEH__?: typeof import("@bokeh/bokehjs");
    __HPCPERFSTATS_BOKEH_SMOKE_READY__?: boolean;
  }
}

/** Playwright-only: bundled @bokeh/bokehjs via Next export (same chunk graph as production). */
export default function BokehPlaywrightSmokePage() {
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      applyBokehResizeObserverDeferral();
      const Bokeh = await import("@bokeh/bokehjs");
      if (cancelled) return;
      window.__HPCPERFSTATS_NEXT_BOKEH__ = Bokeh;
      window.__HPCPERFSTATS_BOKEH_SMOKE_READY__ = true;
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return <div id="plot" style={{ width: 280, height: 200 }} />;
}
