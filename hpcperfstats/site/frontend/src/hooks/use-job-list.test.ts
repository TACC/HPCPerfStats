import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useJobListQuery } from "./use-job-list";

vi.mock("@/api/generated/jobs/jobs", () => ({
  useJobsRetrieve: vi.fn(),
}));

import { useJobsRetrieve } from "@/api/generated/jobs/jobs";

describe("useJobListQuery", () => {
  it("maps Orval query to view contract", () => {
    vi.mocked(useJobsRetrieve).mockReturnValue({
      data: { nj: 2 },
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve>);

    const { result } = renderHook(() => useJobListQuery({ page: "1" }));

    expect(useJobsRetrieve).toHaveBeenCalledWith(
      { page: "1", include_filter_options: 0 },
      expect.objectContaining({ query: expect.objectContaining({ placeholderData: expect.anything() }) }),
    );
    expect(result.current.data).toEqual({ nj: 2 });
    expect(result.current.error).toBe(null);
    expect(result.current.initialLoading).toBe(false);
    expect(result.current.tableBusy).toBe(false);
  });

  it("exposes jobsFetching from isFetching", () => {
    vi.mocked(useJobsRetrieve).mockReturnValue({
      data: { nj: 2 },
      error: null,
      isLoading: false,
      isFetching: true,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve>);

    const { result } = renderHook(() => useJobListQuery({ page: "1" }));

    expect(result.current.jobsFetching).toBe(true);
    expect(result.current.tableBusy).toBe(true);
  });

  it("surfaces fetch errors", () => {
    vi.mocked(useJobsRetrieve).mockReturnValue({
      data: undefined,
      error: new Error("list failed"),
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve>);

    const { result } = renderHook(() => useJobListQuery({}));

    expect(result.current.data).toBe(null);
    expect(result.current.error).toBe("list failed");
  });
});
