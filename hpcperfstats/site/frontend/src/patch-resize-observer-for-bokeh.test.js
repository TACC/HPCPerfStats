import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyBokehResizeObserverDeferral,
  resetBokehResizeObserverPatchForTests,
} from "./patch-resize-observer-for-bokeh";

describe("applyBokehResizeObserverDeferral", () => {
  beforeEach(() => {
    resetBokehResizeObserverPatchForTests();
  });
  afterEach(() => {
    resetBokehResizeObserverPatchForTests();
  });

  it("defers ResizeObserver delivery past the microtask queue (Bokeh build_view race)", async () => {
    class SyncFireRO {
      constructor(callback) {
        this._callback = callback;
      }
      observe() {
        this._callback([], this);
      }
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", SyncFireRO);

    applyBokehResizeObserverDeferral();

    const order = [];
    const ro = new window.ResizeObserver(() => order.push("resize"));
    order.push("before-observe");
    ro.observe(document.body);
    order.push("after-observe");
    expect(order).toEqual(["before-observe", "after-observe"]);

    await Promise.resolve();
    expect(order).toEqual(["before-observe", "after-observe"]);
    await new Promise((r) => setTimeout(r, 0));
    expect(order).toEqual(["before-observe", "after-observe", "resize"]);
  });

  it("is idempotent", () => {
    const RO = window.ResizeObserver;
    applyBokehResizeObserverDeferral();
    const first = window.ResizeObserver;
    applyBokehResizeObserverDeferral();
    expect(window.ResizeObserver).toBe(first);
    resetBokehResizeObserverPatchForTests();
    expect(window.ResizeObserver).toBe(RO);
  });
});
