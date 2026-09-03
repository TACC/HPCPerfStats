"use client";

import { useEffect } from "react";
import { applyBokehResizeObserverDeferral } from "@/patch-resize-observer-for-bokeh";

/** Playwright-only: vendored UMD Bokeh via Next export (same load path as production). */
export default function BokehPlaywrightSmokePage() {
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      applyBokehResizeObserverDeferral();
      const mod = await import("@/bokehjs-bundle");
      if (cancelled) return;
      const Bokeh = await mod.loadBokehRuntime();
      if (cancelled) return;
      // Fail closed in console if vendor UMD / registration regresses.
      const missing = ["Grid", "DocumentConfig", "Figure"].filter(
        (name) => Bokeh.Models?.[name] == null && Bokeh.Models?.get?.(name) == null,
      );
      if (missing.length > 0) {
        console.error(
          `could not resolve type '${missing[0]}', which could be due to a widget or a custom model not being registered before first usage`,
        );
      }
      window.__HPCPERFSTATS_NEXT_BOKEH__ = Bokeh;
      window.Bokeh = Bokeh;
      window.__HPCPERFSTATS_BOKEH_SMOKE_READY__ = true;
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return <div id="plot" style={{ width: 280, height: 200 }} />;
}
