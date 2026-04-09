/**
 * Bokeh 3.9 attaches ResizeObserver in UIElementView.initialize() and observes
 * `el` immediately. Browsers may deliver the first resize callback synchronously
 * while nested views are still in build_view/lazy_initialize, so AxisView.get_size
 * runs before `ranges` exist → `can't access property "is_valid", e is undefined`
 * and "FigureView wasn't built properly".
 *
 * One macrotask (setTimeout(0)) is often *still* too soon: Bokeh's view tree finishes
 * ``lazy_initialize`` across multiple frames, so ``AxisView.ranges`` can remain
 * undefined when our deferred callback runs (stack shows app bundle ``t`` → Bokeh
 * ``after_resize`` → crash). Use **two** ``requestAnimationFrame`` hops so the
 * callback runs after layout/paint work for the current embed; fall back to
 * ``setTimeout`` only when rAF is missing.
 *
 * Applied once before dynamic import of @bokeh/bokehjs (see bokehInit.js).
 */

const PATCH_FLAG = "__hpcperfstatsResizeObserverDeferred";

let nativeResizeObserver = null;

export function scheduleBokehSafeResizeObserverCallback(callback, entries, observer) {
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        callback(entries, observer);
      });
    });
    return;
  }
  setTimeout(() => {
    callback(entries, observer);
  }, 0);
}

export function applyBokehResizeObserverDeferral() {
  if (typeof window === "undefined" || typeof ResizeObserver === "undefined") {
    return;
  }
  if (window[PATCH_FLAG]) {
    return;
  }
  nativeResizeObserver = window.ResizeObserver;
  window[PATCH_FLAG] = true;
  const Native = nativeResizeObserver;

  function DeferredResizeObserver(callback) {
    return new Native((entries, observer) => {
      scheduleBokehSafeResizeObserverCallback(callback, entries, observer);
    });
  }
  DeferredResizeObserver.prototype = Native.prototype;
  window.ResizeObserver = DeferredResizeObserver;
}

/** Vitest only: restore native ResizeObserver between tests. */
export function resetBokehResizeObserverPatchForTests() {
  if (typeof window === "undefined") return;
  if (nativeResizeObserver) {
    window.ResizeObserver = nativeResizeObserver;
    nativeResizeObserver = null;
  }
  delete window[PATCH_FLAG];
}
