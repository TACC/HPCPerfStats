import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useJobMonitorQuery } from "./use-job-monitor";

vi.mock("@/api/generated/monitor/monitor", () => ({
  useJobMonitorRetrieve: vi.fn(),
}));

import { useJobMonitorRetrieve } from "@/api/generated/monitor/monitor";

describe("useJobMonitorQuery", () => {
  it("loads monitor rows for window days", () => {
    vi.mocked(useJobMonitorRetrieve).mockReturnValue({
      data: { results: [{ username: "alice" }] },
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobMonitorRetrieve>);

    const { result } = renderHook(() => useJobMonitorQuery(30));

    expect(useJobMonitorRetrieve).toHaveBeenCalledWith(
      { days: 30 },
      expect.objectContaining({
        query: expect.objectContaining({
          enabled: true,
          placeholderData: expect.any(Function),
        }),
      }),
    );
    expect(result.current.data?.results?.[0]?.username).toBe("alice");
  });

  it("exposes initialLoading and tableBusy for progressive render", () => {
    vi.mocked(useJobMonitorRetrieve).mockReturnValue({
      data: { results: [{ username: "alice" }] },
      error: null,
      isLoading: false,
      isFetching: true,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobMonitorRetrieve>);

    const { result } = renderHook(() => useJobMonitorQuery(30));

    expect(result.current.initialLoading).toBe(false);
    expect(result.current.tableBusy).toBe(true);
  });
});
