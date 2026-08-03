import { afterEach, describe, expect, it, vi } from "vitest";
import {
  delayMs,
  defaultDeferEmbedUntilVisible,
  defaultEmbedSettleAfterIdleMs,
  isVitestLike,
} from "./bokeh-embed-defaults";

describe("bokeh-embed-defaults Vitest env contract", () => {
  it("detects Vitest via import.meta.env.VITEST (Next 16.3 ImportMetaEnv merge)", () => {
    expect(isVitestLike()).toBe(true);
    expect(defaultDeferEmbedUntilVisible()).toBe(false);
    expect(defaultEmbedSettleAfterIdleMs()).toBe(0);
  });
});

describe("bokeh-embed-defaults delayMs", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves immediately for non-positive values", async () => {
    await expect(delayMs(0)).resolves.toBeUndefined();
    await expect(delayMs(-1)).resolves.toBeUndefined();
  });

  it("waits approximately the requested milliseconds", async () => {
    vi.useFakeTimers();
    const done = vi.fn();
    const p = delayMs(80).then(done);
    await vi.advanceTimersByTimeAsync(79);
    await Promise.resolve();
    expect(done).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await p;
    expect(done).toHaveBeenCalledTimes(1);
  });
});
