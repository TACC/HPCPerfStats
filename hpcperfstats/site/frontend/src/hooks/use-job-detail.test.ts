import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { jobDetailPlaceholderData, useJobDetailQuery } from "./use-job-detail";
import type { JobDetailResponse } from "@/api/generated/models/jobDetailResponse";
import { orvalOkEnvelope } from "@/api/orval-response";

vi.mock("@/api/generated/jobs/jobs", () => ({
  useJobsRetrieve2: vi.fn(),
}));

import { useJobsRetrieve2 } from "@/api/generated/jobs/jobs";

describe("jobDetailPlaceholderData", () => {
  it("keeps placeholder only when previous payload matches pk", () => {
    const prev = orvalOkEnvelope({ job_data: { jid: "12345" } } as JobDetailResponse);
    expect(jobDetailPlaceholderData("12345", prev)).toBe(prev);
    expect(jobDetailPlaceholderData("99999", prev)).toBeUndefined();
  });
});

describe("useJobDetailQuery", () => {
  it("uses pk-scoped placeholderData in Orval query options", () => {
    vi.mocked(useJobsRetrieve2).mockReturnValue({
      data: { job_data: { jid: 1 } },
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useJobsRetrieve2>);

    renderHook(() => useJobDetailQuery("1"));

    const options = vi.mocked(useJobsRetrieve2).mock.calls[0]?.[2];
    expect(options?.query?.placeholderData).toEqual(expect.any(Function));
    const placeholder = options?.query?.placeholderData as (
      prev: ReturnType<typeof orvalOkEnvelope<JobDetailResponse>> | undefined,
    ) => ReturnType<typeof orvalOkEnvelope<JobDetailResponse>> | undefined;
    expect(
      placeholder(orvalOkEnvelope({ job_data: { jid: "1" } } as JobDetailResponse)),
    ).toEqual(orvalOkEnvelope({ job_data: { jid: "1" } }));
    expect(
      placeholder(orvalOkEnvelope({ job_data: { jid: "2" } } as JobDetailResponse)),
    ).toBeUndefined();
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
