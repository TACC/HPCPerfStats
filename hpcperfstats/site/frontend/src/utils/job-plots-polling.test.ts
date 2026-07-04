import { describe, expect, it, vi } from "vitest";
import { scheduleJobPlotsRetry } from "./job-plots-polling";

describe("scheduleJobPlotsRetry", () => {
  it("invokes fetch after delay when not cancelled", async () => {
    vi.useFakeTimers();
    const fetchFn = vi.fn();
    scheduleJobPlotsRetry(fetchFn, 0.1, () => false);
    expect(fetchFn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("does not invoke fetch when cancelled", async () => {
    vi.useFakeTimers();
    const fetchFn = vi.fn();
    scheduleJobPlotsRetry(fetchFn, 0.05, () => true);
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchFn).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("cancel clears a pending retry timer", async () => {
    vi.useFakeTimers();
    const fetchFn = vi.fn();
    const cancel = scheduleJobPlotsRetry(fetchFn, 0.1, () => false);
    cancel();
    await vi.advanceTimersByTimeAsync(300);
    expect(fetchFn).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
