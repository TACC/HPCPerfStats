import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import JobDetail, {
  jobPlotEntryEqual,
  jobPlotStatesEqual,
  mergeProgressiveJobPlotsState,
} from "./JobDetail";
import * as apiModule from "../api";
import { SessionContext } from "../session-context";

const minimalJobDetailResponse = {
  job_data: {
    jid: 12345,
    username: "testuser",
    account: "testacct",
    start_time: "2024-01-01T00:00:00Z",
    end_time: "2024-01-01T01:00:00Z",
    runtime: 3600,
    timelimit: 7200,
    queue: "normal",
    jobname: "testjob",
    state: "COMPLETED",
    ncores: 32,
    nhosts: 2,
  },
  host_list: [],
  fsio: {},
  xalt_data: {},
  schema: {},
  client_url: null,
  server_url: null,
  gpu_active: null,
  gpu_utilization_max: null,
  gpu_utilization_mean: null,
  gpu_count: null,
  metrics_list: [],
  proc_list: [],
};

/** Batch payload from `GET .../plots/?progressive=1` when all plots are ready. */
const minimalBatchPlotsResponse = {
  status: "ready",
  progressive: true,
  loading_plots: [],
  mscript: "",
  mdiv: "",
  mplot_item: null,
  mplot_unavailable_reason: null,
  hscript: "",
  hdiv: "",
  hplot_item: null,
  hplot_unavailable_reason: null,
  rscript: "",
  rdiv: "",
  rplot_item: null,
  rplot_unavailable_reason: null,
  grscript: "",
  grdiv: "",
  grplot_item: null,
  grplot_unavailable_reason: null,
};

function batchPlotsResponseWithRoots() {
  return {
    ...minimalBatchPlotsResponse,
    mplot_item: { doc: {}, root_ids: ["summary_plot-root"] },
    hplot_item: { doc: {}, root_ids: ["heatmap-root"] },
    rplot_item: { doc: {}, root_ids: ["roofline-root"] },
    grplot_item: { doc: {}, root_ids: ["gpu_roofline-root"] },
  };
}

function renderJobDetail(pk = "12345", session = { is_staff: false }) {
  return render(
    <SessionContext.Provider value={session}>
      <MemoryRouter initialEntries={[`/job/${pk}`]}>
        <Routes>
          <Route path="job/:pk" element={<JobDetail />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>
  );
}

describe("JobDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.Bokeh;
  });

  function mockAllPlotCallsReady() {
    return vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(minimalBatchPlotsResponse);
  }

  it("labels the fsio table column Shared File System", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(minimalJobDetailResponse);
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(minimalJobDetailResponse);
    mockAllPlotCallsReady();
    renderJobDetail("12345");
    await waitFor(() => {
      expect(
        screen.getByRole("columnheader", { name: "Shared File System" }),
      ).toBeInTheDocument();
    });
  });

  it("shows Sample Count for staff when API includes staff_metrics_distinct_time_count", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue({
      ...minimalJobDetailResponse,
      staff_metrics_distinct_time_count: 1250,
    });
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue({
      ...minimalJobDetailResponse,
      staff_metrics_distinct_time_count: 1250,
    });
    mockAllPlotCallsReady();

    renderJobDetail("12345", { is_staff: true });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByText("Sample Count")).toBeInTheDocument();
    expect(screen.getByText("1,250.00")).toBeInTheDocument();
  });

  it("does not show Sample Count table for non-staff", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue({
      ...minimalJobDetailResponse,
      staff_metrics_distinct_time_count: 999,
    });
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue({
      ...minimalJobDetailResponse,
      staff_metrics_distinct_time_count: 999,
    });
    mockAllPlotCallsReady();

    renderJobDetail("12345", { is_staff: false });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.queryByText("Sample Count")).not.toBeInTheDocument();
  });

  it("shows loading indicator while job detail is fetching", () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockReturnValue(
      new Promise(() => {})
    );
    mockAllPlotCallsReady();

    renderJobDetail();
    expect(
      screen.getByRole("status", { name: /loading job detail/i })
    ).toBeInTheDocument();
  });

  it("loads job detail page before plots and requests plots after detail resolves", async () => {
    const getJobDetailLightSpy = vi
      .spyOn(apiModule.api, "getJobDetailLight")
      .mockResolvedValue(minimalJobDetailResponse);
    const getJobDetailSpy = vi
      .spyOn(apiModule.api, "getJobDetail")
      .mockResolvedValue(minimalJobDetailResponse);
    const getJobPlotsSpy = vi.spyOn(apiModule.api, "getJobPlots").mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(minimalBatchPlotsResponse), 100);
        })
    );

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getAllByRole("link", { name: "12345" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("testjob").length).toBeGreaterThanOrEqual(1);

    expect(getJobDetailLightSpy).toHaveBeenCalledWith("12345");
    expect(getJobPlotsSpy).toHaveBeenCalledWith("12345", null, false, true);

    expect(screen.getByText("Loading job plots…", { hidden: true })).toBeInTheDocument();

    await waitFor(
      () => {
        expect(screen.queryByText("Loading job plots…", { hidden: true })).not.toBeInTheDocument();
      },
      { timeout: 200 }
    );
  });

  it("does not call getJobPlots when getJobDetail fails", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockRejectedValue(
      new Error("Job not found")
    );
    const getJobPlotsSpy = vi
      .spyOn(apiModule.api, "getJobPlots")
      .mockResolvedValue(minimalBatchPlotsResponse);

    renderJobDetail();

    await waitFor(() => {
      expect(screen.getByText(/Error: Job not found/)).toBeInTheDocument();
    });

    expect(getJobPlotsSpy).not.toHaveBeenCalled();
  });

  it("uses a valid bootstrap column class for log link container", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    mockAllPlotCallsReady();

    const { container } = renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });

    expect(container.querySelector(".col-sm-20")).toBeNull();
    expect(container.querySelector("#job-detail-resources")).toBeTruthy();
  });

  it("shows no_data_reason text for staff when a metric has no numeric value", async () => {
    const detailWithMetricMessage = {
      ...minimalJobDetailResponse,
      metrics_list: [
        {
          metric: "avg_freq",
          type: "pmc",
          units: "GHz",
          value: null,
          no_data_reason: "No usable PMC telemetry for average CPU frequency",
        },
      ],
    };
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      detailWithMetricMessage
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      detailWithMetricMessage
    );
    mockAllPlotCallsReady();

    renderJobDetail("12345", { is_staff: true });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(
        screen.getByText("No usable PMC telemetry for average CPU frequency"),
      ).toBeInTheDocument();
    });
  });

  it("shows generic no-data text for non-staff when metric value is missing", async () => {
    const detailWithMetricMessage = {
      ...minimalJobDetailResponse,
      metrics_list: [
        {
          metric: "avg_freq",
          type: "pmc",
          units: "GHz",
          value: null,
          no_data_reason: "No usable PMC telemetry for average CPU frequency",
        },
      ],
    };
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      detailWithMetricMessage
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      detailWithMetricMessage
    );
    mockAllPlotCallsReady();

    renderJobDetail("12345", { is_staff: false });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getAllByText("Data not available.").length).toBeGreaterThan(0);
    });
    expect(
      screen.queryByText("No usable PMC telemetry for average CPU frequency")
    ).not.toBeInTheDocument();
  });

  it("keeps host-level loading message visible while plots API reports loading", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    const getJobPlotsSpy = vi
      .spyOn(apiModule.api, "getJobPlots")
      .mockResolvedValueOnce({
        status: "loading",
        retry_after_seconds: 0,
      })
      .mockResolvedValueOnce(minimalBatchPlotsResponse);

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByText("Loading job plots…", { hidden: true })).toBeInTheDocument();

    await waitFor(() => {
      expect(getJobPlotsSpy).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.queryByText("Loading job plots…", { hidden: true })).not.toBeInTheDocument();
    });
  });

  it("polls progressive job plots and merges partial responses before final ready", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    const partial = {
      status: "partial",
      progressive: true,
      loading_plots: ["heatmap", "roofline", "gpu_roofline"],
      retry_after_seconds: 0,
      mplot_item: { doc: {}, root_ids: ["s-only"] },
      mplot_unavailable_reason: null,
    };
    const getJobPlotsSpy = vi
      .spyOn(apiModule.api, "getJobPlots")
      .mockResolvedValueOnce(partial)
      .mockResolvedValueOnce(minimalBatchPlotsResponse);

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(getJobPlotsSpy).toHaveBeenCalledTimes(2);
    });
    expect(getJobPlotsSpy).toHaveBeenLastCalledWith("12345", null, false, true);
    await waitFor(() => {
      expect(screen.queryByText("Loading job plots…", { hidden: true })).not.toBeInTheDocument();
    });
  });

  it("shows GPU count from monitor when utilization stats are absent", async () => {
    const detailGpuCountOnly = {
      ...minimalJobDetailResponse,
      gpu_active: null,
      gpu_utilization_max: null,
      gpu_utilization_mean: null,
      gpu_count: 4,
    };
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      detailGpuCountOnly
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      detailGpuCountOnly
    );
    mockAllPlotCallsReady();

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByText("Total GPUs allocated:")).toBeInTheDocument();
    expect(screen.getByText("4.00")).toBeInTheDocument();
  });

  it("renders second GPU roofline panel in host-level plots", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    mockAllPlotCallsReady();

    renderJobDetail("12345");
    await waitFor(() => {
      expect(document.querySelectorAll(".bokeh-embed-wrapper").length).toBe(4);
    });
  });

  it("shows expand controls for each host-level plot and closes zoom overlay", async () => {
    window.Bokeh = {
      embed: {
        embed_item: vi.fn(),
      },
    };
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobPlots").mockImplementation(async (_pk, plot, zoom) => {
      if (zoom) {
        return {
          status: "ready",
          plot,
          plot_item: { doc: {}, root_ids: [`${plot}-zoom`] },
          unavailable_reason: null,
        };
      }
      return batchPlotsResponseWithRoots();
    });

    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Plots" }));

    expect(screen.getByRole("heading", { name: "Summary plot" })).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Expand Summary plot" }),
      ).not.toBeDisabled();
    });

    const expandNames = [
      "Expand Summary plot",
      "Expand Heatmap",
      "Expand CPU Roofline",
      "Expand GPU Roofline (PCIe/NvLink)",
    ];
    expandNames.forEach((name) => {
      expect(screen.getByRole("button", { name })).not.toBeDisabled();
    });

    await userEvent.click(screen.getByRole("button", { name: "Expand Summary plot" }));
    expect(
      screen.getByRole("dialog", { name: "Summary plot zoom view" })
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close zoom window" }));
    expect(
      screen.queryByRole("dialog", { name: "Summary plot zoom view" })
    ).not.toBeInTheDocument();
  });

  it("keeps expand disabled until Bokeh embed finishes", async () => {
    window.Bokeh = {
      embed: {
        embed_item: vi.fn(() => new Promise(() => {})),
      },
    };
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobPlots").mockImplementation(async (_pk, plot, zoom) => {
      if (zoom) {
        return {
          status: "ready",
          plot,
          plot_item: { doc: {}, root_ids: [`${plot}-zoom`] },
          unavailable_reason: null,
        };
      }
      return batchPlotsResponseWithRoots();
    });

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Plots" }));
    expect(screen.getByRole("button", { name: "Expand Summary plot" })).toBeDisabled();
  });
});

describe("jobPlotStatesEqual", () => {
  it("returns true when only nested object identities differ but payload matches", () => {
    const a = {
      summary_plot: {
        loading: false,
        plotItem: { doc: { id: "1" }, root_ids: ["r"] },
        unavailableReason: null,
      },
      heatmap: { loading: true, plotItem: null, unavailableReason: null },
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    const b = {
      summary_plot: {
        loading: false,
        plotItem: { doc: { id: "1" }, root_ids: ["r"] },
        unavailableReason: null,
      },
      heatmap: { loading: true, plotItem: null, unavailableReason: null },
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    expect(a.summary_plot.plotItem).not.toBe(b.summary_plot.plotItem);
    expect(jobPlotStatesEqual(a, b)).toBe(true);
  });

  it("returns false when loading flips for one plot", () => {
    const a = {
      summary_plot: { loading: true, plotItem: null, unavailableReason: null },
      heatmap: { loading: true, plotItem: null, unavailableReason: null },
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    const b = {
      ...a,
      summary_plot: { loading: false, plotItem: { x: 1 }, unavailableReason: null },
    };
    expect(jobPlotStatesEqual(a, b)).toBe(false);
  });
});

describe("jobPlotEntryEqual", () => {
  it("compares plotItem by structure when references differ", () => {
    const p = { loading: false, plotItem: { a: 1 }, unavailableReason: null };
    const q = { loading: false, plotItem: { a: 1 }, unavailableReason: null };
    expect(p.plotItem).not.toBe(q.plotItem);
    expect(jobPlotEntryEqual(p, q)).toBe(true);
  });
});

describe("mergeProgressiveJobPlotsState", () => {
  it("marks keys in loading_plots as loading and applies completed plot fields", () => {
    const prev = {
      summary_plot: { loading: true, plotItem: null, unavailableReason: null },
      heatmap: { loading: true, plotItem: null, unavailableReason: null },
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    const resp = {
      status: "partial",
      progressive: true,
      loading_plots: ["heatmap", "roofline", "gpu_roofline"],
      mplot_item: { doc: {}, root_ids: ["s"] },
      mplot_unavailable_reason: null,
    };
    const next = mergeProgressiveJobPlotsState(prev, resp);
    expect(next.summary_plot).toEqual({
      loading: false,
      plotItem: { doc: {}, root_ids: ["s"] },
      unavailableReason: null,
    });
    expect(next.heatmap.loading).toBe(true);
    expect(next.roofline.loading).toBe(true);
    expect(next.gpu_roofline.loading).toBe(true);
  });

  it("retains prior plotItem for plots still listed in loading_plots", () => {
    const prev = {
      summary_plot: { loading: false, plotItem: { a: 1 }, unavailableReason: null },
      heatmap: { loading: true, plotItem: { h: 1 }, unavailableReason: null },
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    const resp = {
      loading_plots: ["heatmap", "roofline", "gpu_roofline"],
      mplot_item: { a: 2 },
      mplot_unavailable_reason: null,
    };
    const next = mergeProgressiveJobPlotsState(prev, resp);
    expect(next.summary_plot.plotItem).toEqual({ a: 2 });
    expect(next.heatmap.plotItem).toEqual({ h: 1 });
  });
});
