import { describe, expect, it, vi } from "vitest";
import { yieldToMainThread } from "./yield-main-thread";

describe("yieldToMainThread", () => {
  it("resolves via setTimeout when scheduler.yield is unavailable", async () => {
    const prev = (globalThis as { scheduler?: unknown }).scheduler;
    delete (globalThis as { scheduler?: unknown }).scheduler;
    const spy = vi.spyOn(globalThis, "setTimeout");
    await yieldToMainThread();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
    if (prev !== undefined) {
      (globalThis as { scheduler?: unknown }).scheduler = prev;
    }
  });

  it("prefers scheduler.yield when present", async () => {
    const yieldFn = vi.fn().mockResolvedValue(undefined);
    (globalThis as { scheduler?: { yield: () => Promise<void> } }).scheduler = {
      yield: yieldFn,
    };
    await yieldToMainThread();
    expect(yieldFn).toHaveBeenCalledTimes(1);
    delete (globalThis as { scheduler?: unknown }).scheduler;
  });
});
