import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import JobDetail from "../JobDetail";
import {
  jobPlotEntryEqual,
  jobPlotStatesEqual,
  mergeProgressiveJobPlotsState,
} from "@/utils/job-detail-plots";
import { useJobDetailQuery } from "@/hooks/use-job-detail";
import { useJobPlotsQuery } from "@/hooks/use-job-plots";
import { axeSeriousViolations } from "@test/vitest/axe-test-utils";
import { nextNavigationMock, resetNextNavigationMock } from "@test/vitest/test-utils/next-navigation-state";
import { renderWithProviders } from "@test/vitest/test-utils/render-with-providers";
import { VALID_BOKEH_JSON_ITEM } from "@test/vitest/test-utils/bokeh-fixtures";
import {
  createEmptyJobPlotsState,
  plotsStateFromBatchResponse,
} from "@/utils/job-detail-plots";
import { replaceTabInHistory } from "@/utils/replace-tab-history";

vi.mock("@/utils/replace-tab-history", () => ({
  replaceTabInHistory: vi.fn(),
}));

vi.mock("@/hooks/use-job-detail", () => ({
  useJobDetailQuery: vi.fn(),
}));

vi.mock("@/hooks/use-job-plots", () => ({
  useJobPlotsQuery: vi.fn(),
}));

vi.mock("../../bokehInit", () => ({
  ensureBokehLoaded: vi.fn(() => Promise.resolve(globalThis.window?.Bokeh)),
}));

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
  multiprecision_cpu_plot_item: null,
  multiprecision_cpu_unavailable_reason:
    "Missing CPU busy-ops mix metrics in job metrics (need positive avg_flops64b / avg_flops32b / avg_arm_int16_ops / avg_arm_int8_ops shares).",
  multiprecision_gpu_plot_item: null,
  multiprecision_gpu_unavailable_reason:
    "Missing GPU busy-pipe mix counters in host_data (no renderable precision mix rows).",
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
    mplot_item: VALID_BOKEH_JSON_ITEM,
    rplot_item: VALID_BOKEH_JSON_ITEM,
    grplot_item: VALID_BOKEH_JSON_ITEM,
  };
}
function setJobDetailQueryMock(
  overrides: Partial<ReturnType<typeof useJobDetailQuery>> = {},
) {
  vi.mocked(useJobDetailQuery).mockReturnValue({
    data: null,
    error: null,
    initialLoading: false,
    detailBusy: false,
    detailsLoading: false,
    detailFetchWarning: false,
    deferParam: "xalt,proc,multiprecision",
    loadFullDetail: vi.fn(),
    loadDetailWithoutDeferParts: vi.fn(),
    refetchDetail: vi.fn(),
    ...overrides,
  });
}

function setJobPlotsQueryMock(
  overrides: Partial<ReturnType<typeof useJobPlotsQuery>> = {},
) {
  vi.mocked(useJobPlotsQuery).mockReturnValue({
    plots: createEmptyJobPlotsState(false),
    plotsLoading: false,
    plotsFetchFailed: false,
    retryJobPlots: vi.fn(),
    ...overrides,
  });
}

function mockAllPlotCallsReady() {
  setJobPlotsQueryMock({
    plots: plotsStateFromBatchResponse(batchPlotsResponseWithRoots()),
    plotsLoading: false,
  });
}



function renderJobDetail(
  pk = "12345",
  session = { is_staff: false },
  search = "",
) {
  resetNextNavigationMock({
    pathname: `/machine/job/${pk}/`,
    params: { pk },
    searchParams: new URLSearchParams(search),
  });
  const query = search ? `?${search}` : "";
  return renderWithProviders(<JobDetail />, {
    session,
    initialPath: `/job/${pk}${query}`,
    withNavigationSync: true,
  });
}

describe("JobDetail", () => {
  beforeEach(() => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    mockAllPlotCallsReady();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.mocked(useJobDetailQuery).mockReset();
    vi.mocked(useJobPlotsQuery).mockReset();
    delete window.Bokeh;
  });


  it("does not render unsafe javascript: client log links", async () => {
    setJobDetailQueryMock({ data: {
      ...minimalJobDetailResponse,
      client_url: "javascript:alert(1)",
      server_url: "https://logs.example/job/1",
    } });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Server Logs" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("link", { name: "Client Logs" })).not.toBeInTheDocument();
  });

  it("labels the fsio table column Shared File System", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(
        screen.getByRole("columnheader", { name: "Shared File System" }),
      ).toBeInTheDocument();
    });
  });

  it("has no serious axe violations when job detail is loaded", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    const view = renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /job 12345/i })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getAllByRole("tabpanel").length).toBeGreaterThan(0);
    });
    const violations = await axeSeriousViolations(view.container);
    // Base UI Tabs can emit aria-controls ids that axe does not resolve in jsdom.
    expect(violations.filter((v) => v.id !== "aria-valid-attr-value")).toEqual([]);
  });

  it("shows Sample Count for staff when API includes staff_metrics_distinct_time_count", async () => {
    setJobDetailQueryMock({ data: {
      ...minimalJobDetailResponse,
      staff_metrics_distinct_time_count: 1250,
    } });

    renderJobDetail("12345", { is_staff: true });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByText("Sample Count")).toBeInTheDocument();
    expect(screen.getByText("1,250.00")).toBeInTheDocument();
  });

  it("shows Artifact schema for staff when API includes staff_artifact_contract", async () => {
    setJobDetailQueryMock({ data: {
      ...minimalJobDetailResponse,
      staff_metrics_distinct_time_count: 1250,
      staff_artifact_contract: {
        current_plot: 11,
        current_detail: 8,
        db_plot: [10, 11],
        db_detail: [],
        note: "Empty/none does not mean plots are missing — Redis may still serve payloads.",
      },
    } });

    renderJobDetail("12345", { is_staff: true });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByText("Artifact schema")).toBeInTheDocument();
    expect(screen.getByText("Runtime schema: plot 11, detail 8")).toBeInTheDocument();
    expect(
      screen.getByText("Stored schema column: plot 10–11, detail none"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not mean plots are missing/i),
    ).toBeInTheDocument();
  });

  it("does not show Sample Count table for non-staff", async () => {
    setJobDetailQueryMock({ data: {
      ...minimalJobDetailResponse,
      staff_metrics_distinct_time_count: 999,
      staff_artifact_contract: {
        current_plot: 11,
        current_detail: 8,
        db_plot: [11],
        db_detail: [8],
      },
    } });

    renderJobDetail("12345", { is_staff: false });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.queryByText("Sample Count")).not.toBeInTheDocument();
    expect(screen.queryByText("Artifact schema")).not.toBeInTheDocument();
  });

  it("shows loading indicator while job detail is fetching", () => {
    setJobDetailQueryMock({ initialLoading: true, data: null });

    renderJobDetail();
    expect(
      screen.getByRole("status", { name: /loading job detail/i })
    ).toBeInTheDocument();
  });

  it("loads job detail page without plots on default metrics tab", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse, initialLoading: false });
    setJobPlotsQueryMock({
      plots: createEmptyJobPlotsState(true),
      plotsLoading: true,
    });

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getAllByText("testjob").length).toBeGreaterThanOrEqual(1);
    expect(useJobPlotsQuery).toHaveBeenCalledWith("12345", false);
  });

  it("disables plot query when job detail fails", async () => {
    setJobDetailQueryMock({ data: null, error: "Job not found", initialLoading: false });

    renderJobDetail();

    await waitFor(() => {
      expect(screen.getByText(/Error: Job not found/)).toBeInTheDocument();
    });

    expect(useJobPlotsQuery).toHaveBeenCalledWith("12345", false);
  });

  it("does not use invalid legacy column class for log link container", async () => {
    setJobDetailQueryMock({ data: 
      minimalJobDetailResponse
     });

    const { container } = renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });

    expect(container.querySelector(".col-sm-20")).toBeNull();
    expect(container.querySelector("#job-detail-resources")).toBeTruthy();
  });

  it("uses one metrics table when only one job-level metric exists", async () => {
    const oneMetric = {
      ...minimalJobDetailResponse,
      metrics_list: [
        {
          metric: "avg_freq",
          type: "pmc",
          units: "GHz",
          value: 2.5,
          no_data_reason: null,
        },
      ],
    };
    setJobDetailQueryMock({ data: oneMetric });

    const { container } = renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { name: "CPU", level: 3 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Network", level: 3 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "GPU", level: 3 })).not.toBeInTheDocument();
    expect(container.querySelector(".job-detail-metrics-two-col")).toBeNull();
    expect(container.querySelectorAll(".job-detail-metrics-table")).toHaveLength(1);
  });

  it("splits job-level metrics row-major into two tables for wide layout when more than one metric", async () => {
    const fourMetrics = {
      ...minimalJobDetailResponse,
      metrics_list: [
        {
          metric: "avg_freq",
          type: "pmc",
          units: "GHz",
          value: 2.5,
          no_data_reason: null,
        },
        {
          metric: "avg_mbw",
          type: "pmc",
          units: "GB/s",
          value: 100,
          no_data_reason: null,
        },
        {
          metric: "avg_cpuusage",
          type: "pmc",
          units: "cores",
          value: 64,
          no_data_reason: null,
        },
        {
          metric: "avg_flops64b",
          type: "pmc",
          units: "GFLOP/s",
          value: 12,
          no_data_reason: null,
        },
      ],
    };
    setJobDetailQueryMock({ data: fourMetrics });

    const { container } = renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });

    const tables = container.querySelectorAll(
      ".job-detail-metrics-two-col .job-detail-metrics-table",
    );
    expect(tables).toHaveLength(2);

    const leftLabels = Array.from(tables[0]?.querySelectorAll("tbody tr") ?? []).map(
      (row) => row.textContent ?? "",
    );
    const rightLabels = Array.from(tables[1]?.querySelectorAll("tbody tr") ?? []).map(
      (row) => row.textContent ?? "",
    );

    expect(leftLabels).toEqual(
      expect.arrayContaining([
        expect.stringContaining("Average effective CPU frequency"),
        expect.stringContaining("Average CPU cores in use"),
      ]),
    );
    expect(rightLabels).toEqual(
      expect.arrayContaining([
        expect.stringContaining("Average DRAM memory bandwidth"),
        expect.stringContaining("Average double-precision FLOP rate"),
      ]),
    );
    expect(leftLabels[0]).toContain("Average effective CPU frequency");
    expect(rightLabels[0]).toContain("Average DRAM memory bandwidth");
    expect(leftLabels[1]).toContain("Average CPU cores in use");
    expect(rightLabels[1]).toContain("Average double-precision FLOP rate");
  });

  it("groups Metrics tab into source subsections with Memory/NUMA under CPU", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        metrics_list: [
          {
            metric: "avg_freq",
            type: "pmc",
            units: "GHz",
            value: 2.5,
            no_data_reason: null,
          },
          {
            metric: "mem_hwm",
            type: "host_mem",
            units: "GB",
            value: 64,
            no_data_reason: null,
          },
          {
            metric: "max_numa_remote_rate",
            type: "host_numa",
            units: null,
            value: 0.1,
            no_data_reason: null,
          },
          {
            metric: "detail_gpu_count",
            type: "gpu",
            units: null,
            value: 4,
            no_data_reason: null,
          },
          {
            metric: "avg_sharedfs_bw",
            type: "lustre_llite",
            units: "MB/s",
            value: 100,
            no_data_reason: null,
          },
          {
            metric: "avg_ibbw",
            type: "host_ib",
            units: "GB/s",
            value: 50,
            no_data_reason: null,
          },
          {
            metric: "job_cpu_gpu_watt_hours",
            type: "job",
            units: "Wh",
            value: 10,
            no_data_reason: null,
          },
        ],
      },
    });

    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });

    const sectionHeadings = screen
      .getAllByRole("heading", { level: 3 })
      .map((el) => el.textContent)
      .filter((t) =>
        ["CPU", "GPU", "File System", "Network", "Misc"].includes(t ?? ""),
      );
    expect(sectionHeadings).toEqual([
      "CPU",
      "GPU",
      "File System",
      "Network",
      "Misc",
    ]);

    const cpuSection = screen.getByRole("heading", { name: "CPU", level: 3 }).parentElement;
    expect(cpuSection?.textContent).toMatch(/Peak process resident memory/);
    expect(cpuSection?.textContent).toMatch(/Peak non-local NUMA memory access rate/);
  });

  it("hides empty GPU section and always shows Network with empty body", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        metrics_list: [
          {
            metric: "avg_freq",
            type: "pmc",
            units: "GHz",
            value: 2.5,
            no_data_reason: null,
          },
        ],
      },
    });

    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "CPU", level: 3 })).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "GPU", level: 3 })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "File System", level: 3 })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Misc", level: 3 })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Network", level: 3 })).toBeInTheDocument();
    const networkSection = screen.getByRole("heading", { name: "Network", level: 3 })
      .parentElement;
    expect(networkSection?.textContent).toMatch(/Data not available/);
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
    setJobDetailQueryMock({ data: 
      detailWithMetricMessage
     });

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
    setJobDetailQueryMock({ data: 
      detailWithMetricMessage
     });

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

  it("keeps host-level loading message visible while plots are loading", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    setJobPlotsQueryMock({
      plots: createEmptyJobPlotsState(true),
      plotsLoading: true,
    });

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByText("Loading job plots…")).toBeInTheDocument();
  });

  it("hides plot loading message when batch plots are ready", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    setJobPlotsQueryMock({
      plots: plotsStateFromBatchResponse(batchPlotsResponseWithRoots()),
      plotsLoading: false,
    });

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.queryByText("Loading job plots…")).not.toBeInTheDocument();
  });

  it("shows GPU count from monitor when utilization stats are absent", async () => {
    const detailGpuCountOnly = {
      ...minimalJobDetailResponse,
      gpu_active: null,
      gpu_utilization_max: null,
      gpu_utilization_mean: null,
      gpu_count: 4,
    };
    setJobDetailQueryMock({ data: 
      detailGpuCountOnly
     });

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByText("Total GPUs allocated:")).toBeInTheDocument();
    expect(screen.getByText("4.00")).toBeInTheDocument();
  });

  it("shows GPU inventory table and util out-of scale when inventory present", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        gpu_active: 2,
        gpu_utilization_max: 160.0,
        gpu_utilization_mean: 80.0,
        gpu_count: 4,
        gpu_inventory: [
          {
            host: "c561-007",
            dev: "0",
            type: "nvidia_gpu",
            util_max: 90.0,
            util_mean: 40.0,
            power_max_w: 250.0,
          },
          {
            host: "c561-007",
            dev: "1",
            type: "nvidia_gpu",
            util_max: 70.0,
            util_mean: 40.0,
            power_max_w: 200.0,
          },
        ],
      },
    });

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job 12345" })).toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { name: "GPU inventory" })).toBeInTheDocument();
    expect(screen.getAllByText("c561-007").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/out of 400\.00/).length).toBeGreaterThanOrEqual(1);
  });

  it("renders one Bokeh embed when a host-level plot tab is selected", async () => {
    window.Bokeh = {
      embed: {
        embed_item: vi.fn(() => Promise.resolve()),
      },
    };
    setJobDetailQueryMock({ data: 
      minimalJobDetailResponse
     });

    renderJobDetail("12345", { is_staff: false }, "tab=summary");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(document.querySelectorAll(".bokeh-embed-wrapper").length).toBe(1);
    });
  });

  it("shows tab order with Summary second and Roofline third", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });

    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    const tabLabels = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabLabels).toEqual([
      "Metrics",
      "Summary plot",
      "Roofline",
      "Multiprecision Mix",
      "Processes",
      "Execution and hosts",
      "Device data",
    ]);
  });

  it("renders both roofline embeds in the shared Roofline tab", async () => {
    window.Bokeh = {
      embed: {
        embed_item: vi.fn(() => Promise.resolve()),
      },
    };
    setJobDetailQueryMock({ data: 
      minimalJobDetailResponse
     });

    renderJobDetail("12345", { is_staff: false }, "tab=roofline");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(document.querySelectorAll(".bokeh-embed-wrapper").length).toBe(2);
    });
  });

  it("shows per-plot loading copy while Bokeh embed is pending on a plot tab", async () => {
    window.Bokeh = {
      embed: {
        embed_item: vi.fn(() => new Promise(() => {})),
      },
    };
    setJobDetailQueryMock({ data: 
      minimalJobDetailResponse
     });
    mockAllPlotCallsReady();

    renderJobDetail("12345", { is_staff: false }, "tab=summary");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/Loading Summary plot/i)).toBeInTheDocument();
    });
  });

  it("shows multiprecision unavailable copy while full job detail fetch is still pending", async () => {
    setJobDetailQueryMock({
      data: minimalJobDetailResponse,
      detailsLoading: true,
    });
    renderJobDetail("12345", { is_staff: false }, "tab=multiprecisionMix");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getAllByText("Unavailable — Data not available.").length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.queryByText(/Loading CPU Multiprecision Mix/i)).not.toBeInTheDocument();
  });

  it("keeps analysis tabs interactive while detailsLoading", async () => {
    setJobDetailQueryMock({
      data: minimalJobDetailResponse,
      detailsLoading: true,
    });
    renderJobDetail("12345", { is_staff: false }, "tab=metrics");

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /Device data/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("tab", { name: /Device data/i }));
    expect(screen.getByRole("tab", { name: /Device data/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("does not render Host-level plot intro on the Multiprecision Mix tab", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    renderJobDetail("12345", { is_staff: false }, "tab=multiprecisionMix");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    expect(screen.queryByText(/Host-level plot for this job/i)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "CPU Multiprecision Mix" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "GPU Multiprecision Mix" })).toBeInTheDocument();
  });

  it("shows staff plot error detail controls on Multiprecision Mix when reasons are present", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    renderJobDetail("12345", { is_staff: true }, "tab=multiprecisionMix");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "Show plot error details" })).toHaveLength(2);
    });
    expect(screen.getAllByRole("button", { name: "Copy error detail" })).toHaveLength(2);
  });

  it("renders both multiprecision mix embeds when tab is selected", async () => {
    window.Bokeh = {
      embed: {
        embed_item: vi.fn(() => Promise.resolve()),
      },
    };
    const withMultiprecision = {
      ...minimalJobDetailResponse,
      multiprecision_cpu_plot_item: VALID_BOKEH_JSON_ITEM,
      multiprecision_cpu_unavailable_reason: null,
      multiprecision_gpu_plot_item: VALID_BOKEH_JSON_ITEM,
      multiprecision_gpu_unavailable_reason: null,
    };
    setJobDetailQueryMock({ data: withMultiprecision });

    renderJobDetail("12345", { is_staff: false }, "tab=multiprecisionMix");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Job data" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(document.querySelectorAll(".bokeh-embed-wrapper").length).toBe(2);
    });
  });

  it("styles entity and device type links with TextLink classes", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        job_data: {
          ...minimalJobDetailResponse.job_data,
          queue: "normal",
        },
        schema: { cpu: ["ctr_a"] },
      },
    });

    renderJobDetail("12345", { is_staff: false }, "tab=device");

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "normal" })).toHaveClass("text-link");
    });
    expect(screen.getByRole("link", { name: "cpu" })).toHaveClass("underline");
  });

  it("syncs tab via history.replaceState helper (not router.replace) when Device data is selected", async () => {
    vi.mocked(replaceTabInHistory).mockClear();
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Device data" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("tab", { name: "Device data" }));

    expect(nextNavigationMock.router.replace).not.toHaveBeenCalled();
    expect(replaceTabInHistory).toHaveBeenCalledWith(
      "/machine/job/12345/",
      expect.any(URLSearchParams),
      "tab",
      "device",
    );
  });

  it("formats avg_cpuusage as job-total out of ncores", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        job_data: { ...minimalJobDetailResponse.job_data, ncores: 4608 },
        metrics_list: [
          {
            metric: "avg_cpuusage",
            value: 3445.05,
            units: "#cores",
            no_data_reason: null,
          },
        ],
      },
    });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByText("3,445.05 out of 4,608.00")).toBeInTheDocument();
    });
  });

  it("shows decoded GPU clock throttle flag text instead of numeric mask", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        metrics_list: [
          {
            metric: "max_gpu_clock_event_reasons",
            value: 7,
            units: "#",
            no_data_reason: null,
          },
        ],
      },
    });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(
        screen.getByText("GPU idle, Application clocks setting, SW power cap"),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText("7.00")).not.toBeInTheDocument();
    expect(screen.queryByText("[#]")).not.toBeInTheDocument();
    expect(
      screen.getByText("Peak GPU clock throttling reasons"),
    ).toBeInTheDocument();
  });

  it("formats detail_gpu_util_mean as value out of gpu_count times 100", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        gpu_count: 40,
        metrics_list: [
          {
            metric: "detail_gpu_util_mean",
            value: 2552.256,
            units: "%",
            no_data_reason: null,
          },
        ],
      },
    });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByText("2,552.26 out of 4,000.00")).toBeInTheDocument();
    });
  });

  it("shows CPU watt-hours at the top of Resources when present and job has no GPUs", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        gpu_count: null,
        metrics_list: [
          {
            metric: "job_cpu_gpu_watt_hours",
            value: 12.5,
            units: "Wh",
            no_data_reason: null,
          },
        ],
      },
    });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByText("CPU Watt Hours for Job")).toBeInTheDocument();
      expect(screen.queryByText("CPU+GPU Watt Hours for Job")).not.toBeInTheDocument();
      expect(screen.getByText(/12\.50\s*Wh/)).toBeInTheDocument();
    });
  });

  it("shows CPU+GPU watt-hours at the top of Resources when present and job has GPUs", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        gpu_count: 4,
        metrics_list: [
          {
            metric: "job_cpu_gpu_watt_hours",
            value: 12.5,
            units: "Wh",
            no_data_reason: null,
          },
        ],
      },
    });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByText("CPU+GPU Watt Hours for Job")).toBeInTheDocument();
      expect(screen.getByText(/12\.50\s*Wh/)).toBeInTheDocument();
    });
  });

  it("renders multi-column Processes table for object proc_list entries", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        proc_list: [
          {
            host: "c001",
            proc: "streams_2.exe",
            uid: 1001,
            vm_rss: 2048,
            vm_hwm: 4096,
            vm_size: 8192,
            threads: 4,
          },
          {
            host: "c002",
            proc: "streams_2.exe",
            uid: 1001,
            vm_rss: 1024,
            vm_hwm: 2048,
            vm_size: 4096,
            threads: 2,
          },
        ],
      },
    });
    renderJobDetail("12345", { is_staff: false }, "tab=processes");
    await waitFor(() => {
      expect(screen.getByText("streams_2.exe")).toBeInTheDocument();
      expect(screen.getByText(/2(?:\.00)? hosts/i)).toBeInTheDocument();
      expect(screen.getByText(/avg RSS/i)).toBeInTheDocument();
    });
  });

  it("shows Summary metric help strip with VariableInfoLabel fallbacks", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse });
    renderJobDetail("12345", { is_staff: false }, "tab=summary");
    await waitFor(() => {
      expect(screen.getByText(/Metric help/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Help: cpu/i })).toBeInTheDocument();
    });
  });

  it("combines DP and SP effective vector width into one Metrics row", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        metrics_list: [
          {
            metric: "avg_vector_width_64b",
            type: "pmc",
            value: 12,
            units: null,
            no_data_reason: null,
          },
          {
            metric: "avg_vector_width_32b",
            type: "pmc",
            value: 8,
            units: null,
            no_data_reason: null,
          },
        ],
      },
    });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByText("Effective vector width (DP / SP)")).toBeInTheDocument();
      expect(screen.getByText("12.00 / 8.00")).toBeInTheDocument();
    });
    expect(screen.queryByText("Effective vector width (double precision)")).not.toBeInTheDocument();
    const cpuSection = screen.getByRole("heading", { name: "CPU", level: 3 }).parentElement;
    expect(cpuSection?.textContent).toContain("Effective vector width (DP / SP)");
  });

  it("labels dual FSIO rows as Lustre and NFS", async () => {
    setJobDetailQueryMock({
      data: {
        ...minimalJobDetailResponse,
        fsio: {
          llite: [10, 20, 1, 2],
          nfs: [3, 4, 0.5, 1],
        },
      },
    });
    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByText("Lustre")).toBeInTheDocument();
      expect(screen.getByText("NFS")).toBeInTheDocument();
    });
  });

  it("deep-links to Device data without full-page skeleton when data exists", async () => {
    setJobDetailQueryMock({ data: minimalJobDetailResponse, initialLoading: false });
    renderJobDetail("12345", { is_staff: false }, "tab=device");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /job 12345/i })).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("Loading job detail")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Device data" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
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
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    const b = {
      summary_plot: {
        loading: false,
        plotItem: { doc: { id: "1" }, root_ids: ["r"] },
        unavailableReason: null,
      },
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    expect(a.summary_plot.plotItem).not.toBe(b.summary_plot.plotItem);
    expect(jobPlotStatesEqual(a, b)).toBe(true);
  });

  it("returns false when loading flips for one plot", () => {
    const a = {
      summary_plot: { loading: true, plotItem: null, unavailableReason: null },
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
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    const resp = {
      status: "partial",
      progressive: true,
      loading_plots: ["roofline", "gpu_roofline"],
      mplot_item: { doc: {}, root_ids: ["s"] },
      mplot_unavailable_reason: null,
    };
    const next = mergeProgressiveJobPlotsState(prev, resp);
    expect(next.summary_plot).toEqual({
      loading: false,
      plotItem: { doc: {}, root_ids: ["s"] },
      unavailableReason: null,
    });
    expect(next.roofline.loading).toBe(true);
    expect(next.gpu_roofline.loading).toBe(true);
  });

  it("retains prior plotItem for plots still listed in loading_plots", () => {
    const prev = {
      summary_plot: { loading: false, plotItem: { a: 1 }, unavailableReason: null },
      roofline: { loading: true, plotItem: null, unavailableReason: null },
      gpu_roofline: { loading: true, plotItem: null, unavailableReason: null },
    };
    const resp = {
      loading_plots: ["roofline", "gpu_roofline"],
      mplot_item: { a: 2 },
      mplot_unavailable_reason: null,
    };
    const next = mergeProgressiveJobPlotsState(prev, resp);
    expect(next.summary_plot.plotItem).toEqual({ a: 2 });
  });
});
