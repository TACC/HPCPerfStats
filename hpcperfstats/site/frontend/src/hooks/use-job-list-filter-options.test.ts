import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useJobListFilterOptions } from "./use-job-list-filter-options";

vi.mock("@/api/generated/jobs/jobs", () => ({
  useJobsFilterOptionsRetrieve: vi.fn(),
}));

import { useJobsFilterOptionsRetrieve } from "@/api/generated/jobs/jobs";

describe("useJobListFilterOptions", () => {
  it("maps filter options from the secondary endpoint", () => {
    vi.mocked(useJobsFilterOptionsRetrieve).mockReturnValue({
      data: {
        filter_options: {
          queues: ["normal"],
          states: ["COMPLETED"],
          usernames: ["alice"],
          accounts: ["acct"],
        },
      },
      error: null,
      isLoading: false,
      isFetching: false,
    } as ReturnType<typeof useJobsFilterOptionsRetrieve>);

    const { result } = renderHook(() => useJobListFilterOptions({ page: "1" }, true));

    expect(useJobsFilterOptionsRetrieve).toHaveBeenCalledWith(
      { page: "1" },
      { query: { enabled: true, select: expect.any(Function) } },
    );
    expect(result.current.filterOptions).toEqual({
      queues: ["normal"],
      states: ["COMPLETED"],
      usernames: ["alice"],
      accounts: ["acct"],
    });
    expect(result.current.optionsLoading).toBe(false);
    expect(result.current.error).toBe(null);
  });

  it("exposes optionsLoading while the first fetch is in flight", () => {
    vi.mocked(useJobsFilterOptionsRetrieve).mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isFetching: true,
    } as ReturnType<typeof useJobsFilterOptionsRetrieve>);

    const { result } = renderHook(() => useJobListFilterOptions({ page: "1" }, true));

    expect(result.current.optionsLoading).toBe(true);
    expect(result.current.filterOptions).toBe(null);
  });

  it("does not fetch when disabled", () => {
    vi.mocked(useJobsFilterOptionsRetrieve).mockReturnValue({
      data: undefined,
      error: null,
      isLoading: false,
      isFetching: false,
    } as ReturnType<typeof useJobsFilterOptionsRetrieve>);

    renderHook(() => useJobListFilterOptions({ page: "1" }, false));

    expect(useJobsFilterOptionsRetrieve).toHaveBeenCalledWith(
      { page: "1" },
      { query: { enabled: false, select: expect.any(Function) } },
    );
  });

  it("forwards end_time__date from list API params", () => {
    vi.mocked(useJobsFilterOptionsRetrieve).mockReturnValue({
      data: { filter_options: { queues: [] } },
      error: null,
      isLoading: false,
      isFetching: false,
    } as ReturnType<typeof useJobsFilterOptionsRetrieve>);

    renderHook(() =>
      useJobListFilterOptions({ end_time__date: "2024-01-15", page: "1" }, true),
    );

    expect(useJobsFilterOptionsRetrieve).toHaveBeenCalledWith(
      { end_time__date: "2024-01-15", page: "1" },
      { query: { enabled: true, select: expect.any(Function) } },
    );
  });
});
