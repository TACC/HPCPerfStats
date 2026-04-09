/**
 * Bokeh 3.9 attaches ResizeObserver in UIElementView.initialize() and observes
 * `el` immediately. Browsers may deliver the first resize callback synchronously
 * while nested views are still in build_view/lazy_initialize, so AxisView.get_size
 * runs before `ranges` exist → `can't access property "is_valid", e is undefined`
 * and "FigureView wasn't built properly".
 *
 * Two ``requestAnimationFrame`` hops are still too soon on some deployments
 * (e.g. Vista): Bokeh 3.9 finishes ``lazy_initialize`` / promise-linked view work
 * across more than two frames, so ``AxisView`` can read ``range.is_valid`` before
 * ``ranges`` exist (stack: patched RO → ``after_resize`` → ``is_renderable``).
 * Defer with **three** ``requestAnimationFrame`` hops, then **setTimeout(0)** so
 * the callback runs in a fresh macrotask after embed microtasks settle. When rAF
 * is missing, use ``setTimeout`` only.
 *
 * Applied once before dynamic import of @bokeh/bokehjs (see bokehInit.js).
 */

const PATCH_FLAG = "__hpcperfstatsResizeObserverDeferred";

let nativeResizeObserver = null;

export function scheduleBokehSafeResizeObserverCallback(callback, entries, observer) {
  function runCallback() {
    callback(entries, observer);
  }
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setTimeout(runCallback, 0);
        });
      });
    });
    return;
  }
  setTimeout(runCallback, 0);
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
