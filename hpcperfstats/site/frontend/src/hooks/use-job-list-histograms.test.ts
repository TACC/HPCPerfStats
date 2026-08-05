import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/api-error";
import { orvalOkEnvelope } from "@/api/orval-response";
import {
  JOB_LIST_HISTOGRAM_DEBOUNCE_MS,
  JOB_LIST_HISTOGRAM_JOBS_IDLE_POLL_MS,
  useJobListHistograms,
} from "./use-job-list-histograms";

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
  return orvalOkEnvelope({
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
  });
}

async function advanceDebounce() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(JOB_LIST_HISTOGRAM_DEBOUNCE_MS);
  });
}

describe("useJobListHistograms", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockReset();
    vi.useRealTimers();
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

    await advanceDebounce();

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });

    rerender({ params: STABLE_PARAMS, reloadKey: 0, enabled: true });
    rerender({ params: STABLE_PARAMS, reloadKey: 0, enabled: true });

    expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
  });

  it("debounces rapid filter param changes into one batch call", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { rerender } = renderHook(
      ({ params }) => useJobListHistograms(params, 0, true),
      { initialProps: { params: STABLE_PARAMS } },
    );

    rerender({ params: { ...STABLE_PARAMS, queue: "normal" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(JOB_LIST_HISTOGRAM_DEBOUNCE_MS - 100);
    });
    rerender({ params: { ...STABLE_PARAMS, queue: "debug" } });
    await advanceDebounce();

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });
    expect(jobsHistogramsBatchRetrieve.mock.calls[0]?.[0]).toMatchObject({
      queue: "debug",
    });
  });

  it("waits for jobsFetching to settle before batch fetch", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { rerender } = renderHook(
      ({ jobsFetching }) => useJobListHistograms(STABLE_PARAMS, 0, true, jobsFetching),
      { initialProps: { jobsFetching: true } },
    );

    await advanceDebounce();
    expect(jobsHistogramsBatchRetrieve).not.toHaveBeenCalled();

    // Idle wait uses a ref + poll — flipping jobsFetching must not re-run the effect.
    rerender({ jobsFetching: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(JOB_LIST_HISTOGRAM_JOBS_IDLE_POLL_MS);
    });

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });
  });

  it("does not refetch when jobsFetching toggles with stable paramsKey", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { rerender } = renderHook(
      ({ jobsFetching }) => useJobListHistograms(STABLE_PARAMS, 0, true, jobsFetching),
      { initialProps: { jobsFetching: false } },
    );

    await advanceDebounce();
    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });

    rerender({ jobsFetching: true });
    rerender({ jobsFetching: false });
    rerender({ jobsFetching: true });
    rerender({ jobsFetching: false });
    await advanceDebounce();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(JOB_LIST_HISTOGRAM_JOBS_IDLE_POLL_MS * 4);
    });

    expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
  });

  it("keeps histograms reference when batch payload fingerprint is unchanged", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { result, rerender } = renderHook(
      ({ reloadKey }) => useJobListHistograms(STABLE_PARAMS, reloadKey, true),
      { initialProps: { reloadKey: 0 } },
    );

    await advanceDebounce();
    await waitFor(() => {
      expect(result.current.histograms?.length).toBeGreaterThan(0);
    });
    const prior = result.current.histograms;

    rerender({ reloadKey: 1 });
    await advanceDebounce();
    await waitFor(() => {
      expect(result.current.histogramsUpdating).toBe(false);
    });

    expect(result.current.histograms).toBe(prior);
    expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(2);
  });

  it("does not fetch when disabled", async () => {
    renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, false));
    await advanceDebounce();
    expect(jobsHistogramsBatchRetrieve).not.toHaveBeenCalled();
  });

  it("refetches when reloadKey changes", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { rerender } = renderHook(
      ({ reloadKey }) => useJobListHistograms(STABLE_PARAMS, reloadKey, true),
      { initialProps: { reloadKey: 0 } },
    );

    await advanceDebounce();

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });

    rerender({ reloadKey: 1 });
    await advanceDebounce();

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

    await advanceDebounce();

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

    await advanceDebounce();

    await waitFor(() => {
      expect(result.current.batchError).toBe("Too many jobs for histogram generation.");
    });
    expect(result.current.metricHistStatus.runtime.error).toBe(
      "Too many jobs for histogram generation.",
    );
  });

  it("maps legacy nj=0 empty batch to no-jobs message", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(
      orvalOkEnvelope({
      nj: 0,
      histogram_nj: 0,
      histogram_sampled: false,
      histograms: [],
      }),
    );

    const { result } = renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, true));

    await advanceDebounce();

    await waitFor(() => {
      expect(result.current.batchError).toBe("No jobs matched this query.");
    });
    expect(result.current.metricHistStatus.runtime.error).toBe(
      "No jobs matched this query.",
    );
  });

  it("surfaces plot_unavailable_reason on metric status when plots are null", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(
      orvalOkEnvelope({
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
      }),
    );

    const { result } = renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, true));

    await advanceDebounce();

    await waitFor(() => {
      expect(result.current.metricHistStatus.runtime.error).toContain("runtime");
    });
    expect(result.current.metricHistStatus.nhosts.error).toBeNull();
  });

  it("does not refetch when listApiParams object identity changes but paramsKey is unchanged", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { rerender } = renderHook(
      ({ params }) => useJobListHistograms(params, 0, true),
      { initialProps: { params: STABLE_PARAMS } },
    );

    await advanceDebounce();

    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });

    rerender({ params: { ...STABLE_PARAMS } });
    rerender({ params: { page: "1", order_by: "-end_time" } });
    await advanceDebounce();

    expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
  });

  it("sets histogramsUpdating while a debounced fetch is pending", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(mockBatchResponse()), 100);
        }),
    );

    const { result, rerender } = renderHook(
      ({ params }) => useJobListHistograms(params, 0, true),
      { initialProps: { params: STABLE_PARAMS } },
    );

    expect(result.current.histogramsUpdating).toBe(true);

    await advanceDebounce();
    expect(result.current.histogramsUpdating).toBe(true);

    rerender({ params: { ...STABLE_PARAMS, queue: "normal" } });
    expect(result.current.histogramsUpdating).toBe(true);

    await advanceDebounce();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    await waitFor(() => {
      expect(result.current.histogramsUpdating).toBe(false);
    });
  });

  it("keeps prior histogram entries while filter params change before debounce", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { result, rerender } = renderHook(
      ({ params }) => useJobListHistograms(params, 0, true),
      { initialProps: { params: STABLE_PARAMS } },
    );

    await advanceDebounce();

    await waitFor(() => {
      expect(result.current.histograms?.length).toBeGreaterThan(0);
    });
    const prior = result.current.histograms;

    rerender({ params: { ...STABLE_PARAMS, queue: "normal" } });

    expect(result.current.histograms).toBe(prior);
    expect(result.current.histogramsUpdating).toBe(true);
    expect(result.current.metricHistStatus.runtime.loading).toBe(true);
  });

  it("does not refetch when only presentation keys change (caller strips them)", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const filterOnly = { end_time__date: "2024-01-15", queue: "normal" };
    const { rerender } = renderHook(
      ({ params }) => useJobListHistograms(params, 0, true),
      { initialProps: { params: filterOnly } },
    );

    await advanceDebounce();
    await waitFor(() => {
      expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
    });

    // Same filter identity — page/order_by must not be in hist params.
    rerender({ params: { ...filterOnly } });
    await advanceDebounce();
    expect(jobsHistogramsBatchRetrieve).toHaveBeenCalledTimes(1);
  });

  it("sets histogramsUpdating immediately when params change", async () => {
    vi.mocked(jobsHistogramsBatchRetrieve).mockResolvedValue(mockBatchResponse());

    const { result } = renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, true));

    expect(result.current.histogramsUpdating).toBe(true);

    await advanceDebounce();

    await waitFor(() => {
      expect(result.current.histogramsUpdating).toBe(false);
    });
  });
});
