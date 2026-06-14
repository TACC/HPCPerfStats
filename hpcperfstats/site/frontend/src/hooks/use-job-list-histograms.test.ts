import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/api-error";
import { useJobListHistograms } from "./use-job-list-histograms";

vi.mock("@/api/generated/jobs/jobs", () => ({
  jobsHistogramsBatchRetrieve: vi.fn(),
}));

import { jobsHistogramsBatchRetrieve } from "@/api/generated/jobs/jobs";

const STABLE_PARAMS = { page: "1", order_by: "-end_time" };

const VALID_BOKEH_THUMB = {
  root_id: "p1001",
  doc: {
    root_ids: ["p1001"],
    roots: [{ id: "p1001", type: "object", name: "GridPlot" }],
  },
};

function mockBatchResponse(overrides: Record<string, unknown> = {}) {
  return {
    nj: 3,
    histogram_nj: 3,
    histogram_sampled: false,
    histograms: [
      {
        metric: "runtime",
        title: "Runtime",
        plot_item_thumb: VALID_BOKEH_THUMB,
        plot_item_full: VALID_BOKEH_THUMB,
        plot_unavailable_reason: null,
      },
      {
        metric: "nhosts",
        title: "Node count",
        plot_item_thumb: VALID_BOKEH_THUMB,
        plot_item_full: VALID_BOKEH_THUMB,
        plot_unavailable_reason: null,
      },
      {
        metric: "queue_wait",
        title: "Queue wait",
        plot_item_thumb: VALID_BOKEH_THUMB,
        plot_item_full: VALID_BOKEH_THUMB,
        plot_unavailable_reason: null,
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

  it("maps legacy nj=0 empty batch to no-jobs message", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue({
      nj: 0,
      histogram_nj: 0,
      histogram_sampled: false,
      histograms: [],
    });

    const { result } = renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, true));

    await waitFor(() => {
      expect(result.current.batchError).toBe("No jobs matched this query.");
    });
    expect(result.current.metricHistStatus.runtime.error).toBe(
      "No jobs matched this query.",
    );
  });

  it("surfaces plot_unavailable_reason on metric status when plots are null", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue({
      nj: 2,
      histogram_nj: 2,
      histogram_sampled: false,
      histograms: [
        {
          metric: "runtime",
          title: "Runtime",
          plot_item_thumb: null,
          plot_item_full: null,
          plot_unavailable_reason:
            "No histogram data available for metric 'runtime' in this query.",
        },
        {
          metric: "nhosts",
          title: "Node count",
          plot_item_thumb: VALID_BOKEH_THUMB,
          plot_item_full: VALID_BOKEH_THUMB,
          plot_unavailable_reason: null,
        },
        {
          metric: "queue_wait",
          title: "Queue wait",
          plot_item_thumb: VALID_BOKEH_THUMB,
          plot_item_full: VALID_BOKEH_THUMB,
          plot_unavailable_reason: null,
        },
      ],
    });

    const { result } = renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, true));

    await waitFor(() => {
      expect(result.current.metricHistStatus.runtime.error).toContain("runtime");
    });
    expect(result.current.metricHistStatus.nhosts.error).toBeNull();
  });
});
