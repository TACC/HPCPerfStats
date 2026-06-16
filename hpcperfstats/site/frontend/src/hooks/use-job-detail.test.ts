import { keepPreviousData } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useJobDetailQuery } from "./use-job-detail";

vi.mock("@/api/generated/jobs/jobs", () => ({
  useJobsRetrieve2: vi.fn(),
}));

import { useJobsRetrieve2 } from "@/api/generated/jobs/jobs";

describe("useJobDetailQuery", () => {
  it("passes keepPreviousData to Orval query options", () => {
    vi.mocked(useJobsRetrieve2).mockReturnValue({
      data: { job_data: { jid: 1 } },
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve2>);

    renderHook(() => useJobDetailQuery("1"));

    expect(useJobsRetrieve2).toHaveBeenCalledWith(
      "1",
      { defer: "xalt,proc,multiprecision" },
      expect.objectContaining({
        query: expect.objectContaining({
          placeholderData: keepPreviousData,
        }),
      }),
    );
  });

  it("does not surface initialLoading when refetching with prior data", () => {
    vi.mocked(useJobsRetrieve2).mockReturnValue({
      data: { job_data: { jid: 1 } },
      error: null,
      isLoading: true,
      isFetching: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve2>);

    const { result } = renderHook(() => useJobDetailQuery("1"));

    expect(result.current.initialLoading).toBe(false);
    expect(result.current.data).toEqual({ job_data: { jid: 1 } });
  });

  it("surfaces initialLoading only before first payload", () => {
    vi.mocked(useJobsRetrieve2).mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isFetching: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve2>);

    const { result } = renderHook(() => useJobDetailQuery("1"));

    expect(result.current.initialLoading).toBe(true);
    expect(result.current.data).toBe(null);
  });

  it("surfaces detailsLoading when defer changes after initial payload", () => {
    vi.mocked(useJobsRetrieve2).mockReturnValue({
      data: { job_data: { jid: 1 } },
      error: null,
      isLoading: false,
      isFetching: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve2>);

    const { result } = renderHook(() => useJobDetailQuery("1"));
    act(() => {
      result.current.loadFullDetail();
    });

    expect(result.current.detailsLoading).toBe(true);
  });

  it("surfaces detailBusy during background refetch with prior data", () => {
    vi.mocked(useJobsRetrieve2).mockReturnValue({
      data: { job_data: { jid: 1 } },
      error: null,
      isLoading: false,
      isFetching: true,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve2>);

    const { result } = renderHook(() => useJobDetailQuery("1"));

    expect(result.current.detailBusy).toBe(true);
    expect(result.current.initialLoading).toBe(false);
  });
});
