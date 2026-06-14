import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/api-error";
import { useJobListHistograms } from "./use-job-list-histograms";

vi.mock("@/api/generated/jobs/jobs", () => ({
  jobsHistogramsBatchRetrieve: vi.fn(),
}));

import { jobsHistogramsBatchRetrieve } from "@/api/generated/jobs/jobs";

const STABLE_PARAMS = { page: "1", order_by: "-end_time" };

function mockBatchResponse(overrides: Record<string, unknown> = {}) {
  return {
    nj: 3,
    histogram_nj: 3,
    histogram_sampled: false,
    histograms: [
      {
        metric: "runtime",
        histogram_bin_edges: [0, 1],
        histogram_counts: [1],
        bokeh_histogram_json_item: { root_id: "runtime", target_id: "runtime", doc: {}, version: "3.0.0" },
      },
      {
        metric: "nhosts",
        histogram_bin_edges: [0, 1],
        histogram_counts: [1],
        bokeh_histogram_json_item: { root_id: "nhosts", target_id: "nhosts", doc: {}, version: "3.0.0" },
      },
      {
        metric: "queue_wait",
        histogram_bin_edges: [0, 1],
        histogram_counts: [1],
        bokeh_histogram_json_item: { root_id: "queue_wait", target_id: "queue_wait", doc: {}, version: "3.0.0" },
      },
    ],
    ...overrides,
  };
}

describe("useJobListHistograms", () => {
  afterEach(() => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockReset();
  });

  it("uses batch endpoint once per stable filter (no refetch loop)", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { rerender } = renderHook(
      ({ params, reloadKey, enabled }) =>
        useJobListHistograms(params, reloadKey, enabled),
      {
        initialProps: { params: STABLE_PARAMS, reloadKey: 0, enabled: true },
      },
    );

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });

    rerender({ params: STABLE_PARAMS, reloadKey: 0, enabled: true });
    rerender({ params: STABLE_PARAMS, reloadKey: 0, enabled: true });

    expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
  });

  it("does not fetch when disabled", async () => {
    renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, false));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(jobsHistogramsBatchRetrieve).not.toHaveBeenCalled();
  });

  it("refetches when reloadKey changes", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { rerender } = renderHook(
      ({ reloadKey }) => useJobListHistograms(STABLE_PARAMS, reloadKey, true),
      { initialProps: { reloadKey: 0 } },
    );

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });

    rerender({ reloadKey: 1 });

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(2);
    });
  });

  it("records sampled histogram metadata from batch response", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(
      mockBatchResponse({
        nj: 12000,
        histogram_nj: 5000,
        histogram_sampled: true,
      }),
    );

    const { result } = renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, true));

    await waitFor(() => {
      expect(result.current.sampleMeta.histogramSampled).toBe(true);
    });
    expect(result.current.sampleMeta.nj).toBe(12000);
    expect(result.current.sampleMeta.histogramNj).toBe(5000);
  });

  it("surfaces API detail text on batch failure", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockRejectedValue(
      new ApiError("histogram_job_count_exceeded", 413, {
        error: "histogram_job_count_exceeded",
        detail: "Too many jobs for histogram generation.",
      }),
    );

    const { result } = renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, true));

    await waitFor(() => {
      expect(result.current.batchError).toBe("Too many jobs for histogram generation.");
    });
    expect(result.current.metricHistStatus.runtime.error).toBe(
      "Too many jobs for histogram generation.",
    );
  });
});
