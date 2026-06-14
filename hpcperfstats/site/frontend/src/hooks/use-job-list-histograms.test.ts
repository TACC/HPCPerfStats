import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useJobListHistograms } from "./use-job-list-histograms";

vi.mock("@/api/generated/jobs/jobs", () => ({
  jobsHistogramsRetrieve: vi.fn(),
}));

import { jobsHistogramsRetrieve } from "@/api/generated/jobs/jobs";

const STABLE_PARAMS = { page: "1", order_by: "-end_time" };

function mockHistogramResponse(metric: string) {
  return {
    metric,
    histogram_bin_edges: [0, 1],
    histogram_counts: [1],
    bokeh_histogram_json_item: { root_id: metric, target_id: metric, doc: {}, version: "3.0.0" },
  };
}

describe("useJobListHistograms", () => {
  afterEach(() => {
    vi.mocked(jobsHistogramsRetrieve).mockReset();
  });

  it("fetches each default metric once per stable filter (no refetch loop)", async () => {
    vi.mocked(jobsHistogramsRetrieve).mockImplementation(async (params) => {
      const metric = String((params as { metric?: string }).metric || "");
      return mockHistogramResponse(metric);
    });

    const { rerender } = renderHook(
      ({ params, reloadKey, enabled }) =>
        useJobListHistograms(params, reloadKey, enabled),
      {
        initialProps: { params: STABLE_PARAMS, reloadKey: 0, enabled: true },
      },
    );

    await waitFor(() => {
      expect(jobsHistogramsRetrieve).toHaveBeenCalledTimes(3);
    });

    rerender({ params: STABLE_PARAMS, reloadKey: 0, enabled: true });
    rerender({ params: STABLE_PARAMS, reloadKey: 0, enabled: true });

    expect(jobsHistogramsRetrieve).toHaveBeenCalledTimes(3);
  });

  it("does not fetch when disabled", async () => {
    renderHook(() => useJobListHistograms(STABLE_PARAMS, 0, false));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(jobsHistogramsRetrieve).not.toHaveBeenCalled();
  });

  it("refetches when reloadKey changes", async () => {
    vi.mocked(jobsHistogramsRetrieve).mockResolvedValue(mockHistogramResponse("runtime"));

    const { rerender } = renderHook(
      ({ reloadKey }) => useJobListHistograms(STABLE_PARAMS, reloadKey, true),
      { initialProps: { reloadKey: 0 } },
    );

    await waitFor(() => {
      expect(jobsHistogramsRetrieve).toHaveBeenCalledTimes(3);
    });

    rerender({ reloadKey: 1 });

    await waitFor(() => {
      expect(jobsHistogramsRetrieve).toHaveBeenCalledTimes(6);
    });
  });
});
