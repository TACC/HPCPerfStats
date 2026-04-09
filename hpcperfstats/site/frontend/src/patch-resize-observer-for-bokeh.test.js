import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyBokehResizeObserverDeferral,
  resetBokehResizeObserverPatchForTests,
  scheduleBokehSafeResizeObserverCallback,
} from "./patch-resize-observer-for-bokeh";

describe("applyBokehResizeObserverDeferral", () => {
  beforeEach(() => {
    resetBokehResizeObserverPatchForTests();
  });
  afterEach(() => {
    resetBokehResizeObserverPatchForTests();
  });

  it("defers ResizeObserver via two requestAnimationFrame hops", () => {
    const rafQueue = [];
    vi.stubGlobal("requestAnimationFrame", (fn) => {
      rafQueue.push(fn);
      return rafQueue.length;
    });

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
    expect(rafQueue.length).toBe(1);

    rafQueue.shift()(0);
    expect(order).toEqual(["before-observe", "after-observe"]);
    expect(rafQueue.length).toBe(1);

    rafQueue.shift()(0);
    expect(order).toEqual(["before-observe", "after-observe", "resize"]);
  });

  it("falls back to setTimeout when requestAnimationFrame is missing", async () => {
    vi.stubGlobal("requestAnimationFrame", undefined);

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
    ro.observe(document.body);
    expect(order).toEqual([]);
    await new Promise((r) => setTimeout(r, 0));
    expect(order).toEqual(["resize"]);
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

describe("scheduleBokehSafeResizeObserverCallback", () => {
  it("runs user callback after two rAF ticks", () => {
    const rafQueue = [];
    vi.stubGlobal("requestAnimationFrame", (fn) => {
      rafQueue.push(fn);
      return rafQueue.length;
    });

    const order = [];
    scheduleBokehSafeResizeObserverCallback(
      () => order.push("cb"),
      [],
      /** @type {any} */ ({}),
    );
    expect(order).toEqual([]);
    rafQueue.shift()(0);
    expect(order).toEqual([]);
    rafQueue.shift()(0);
    expect(order).toEqual(["cb"]);
  });
});
