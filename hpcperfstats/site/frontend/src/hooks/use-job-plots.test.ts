import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { orvalOkEnvelope } from "@/api/orval-response";
import { useJobPlotsQuery } from "./use-job-plots";

vi.mock("@/api/generated/jobs/jobs", () => ({
  jobsPlotsRetrieve: vi.fn(),
}));

import { jobsPlotsRetrieve } from "@/api/generated/jobs/jobs";

describe("useJobPlotsQuery", () => {
  afterEach(() => {
    vi.mocked(jobsPlotsRetrieve).mockReset();
  });

  it("resets plots state when pk changes", async () => {
    vi.mocked(jobsPlotsRetrieve).mockResolvedValue(
      orvalOkEnvelope({
        mplot_item: { type: "plot" },
        status: "ready",
      }),
    );

    const { result, rerender } = renderHook(
      ({ pk, enabled }) => useJobPlotsQuery(pk, enabled),
      { initialProps: { pk: "111", enabled: true } },
    );

    await waitFor(() => {
      expect(result.current.plotsLoading).toBe(false);
    });
    expect(jobsPlotsRetrieve).toHaveBeenCalledWith("111", { progressive: "1" });

    rerender({ pk: "222", enabled: true });

    await waitFor(() => {
      expect(jobsPlotsRetrieve).toHaveBeenCalledWith("222", { progressive: "1" });
    });
    expect(jobsPlotsRetrieve).toHaveBeenCalledTimes(2);
  });

  it("does not fetch when disabled", () => {
    renderHook(() => useJobPlotsQuery("111", false));
    expect(jobsPlotsRetrieve).not.toHaveBeenCalled();
  });

  it("does not update state after unmount", async () => {
    vi.mocked(jobsPlotsRetrieve).mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(
            () =>
              resolve(
                orvalOkEnvelope({
                  mplot_item: { type: "plot" },
                  status: "ready",
                }),
              ),
            50,
          );
        }),
    );

    const { unmount } = renderHook(() => useJobPlotsQuery("111", true));
    unmount();

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });

    expect(jobsPlotsRetrieve).toHaveBeenCalledTimes(1);
  });

  it("cancels scheduled retry after unmount", async () => {
    vi.useFakeTimers();
    vi.mocked(jobsPlotsRetrieve).mockResolvedValue(
      orvalOkEnvelope({
        status: "loading",
        retry_after_seconds: 0.1,
      }),
    );

    const { unmount } = renderHook(() => useJobPlotsQuery("111", true));

    await act(async () => {
      await Promise.resolve();
    });
    expect(jobsPlotsRetrieve).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(jobsPlotsRetrieve).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
