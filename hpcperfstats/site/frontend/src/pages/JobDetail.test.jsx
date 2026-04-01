import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import JobDetail from "./JobDetail";
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

const minimalPlotsResponse = {
  mplot_item: null,
  mplot_unavailable_reason: null,
  hplot_item: null,
  hplot_unavailable_reason: null,
  rplot_item: null,
  rplot_unavailable_reason: null,
  grplot_item: null,
  grplot_unavailable_reason: null,
};

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
  });

  it("shows loading indicator while job detail is fetching", () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockReturnValue(
      new Promise(() => {})
    );
    vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(minimalPlotsResponse);

    renderJobDetail();
    expect(
      screen.getByText("Loading job detail…")
    ).toBeInTheDocument();
  });

  it("loads job detail page before plots and requests plots after detail resolves", async () => {
    const getJobDetailLightSpy = vi
      .spyOn(apiModule.api, "getJobDetailLight")
      .mockResolvedValue(minimalJobDetailResponse);
    const getJobDetailSpy = vi
      .spyOn(apiModule.api, "getJobDetail")
      .mockResolvedValue(minimalJobDetailResponse);
    const getJobPlotsSpy = vi
      .spyOn(apiModule.api, "getJobPlots")
      .mockImplementation(
        () =>
          new Promise((resolve) => {
            setTimeout(() => resolve(minimalPlotsResponse), 100);
          })
      );

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByText("Job Detail")).toBeInTheDocument();
    });
    expect(screen.getByText("12345", { selector: "a" })).toBeInTheDocument();
    expect(screen.getByText("testjob")).toBeInTheDocument();

    expect(getJobDetailLightSpy).toHaveBeenCalledWith("12345");
    expect(getJobPlotsSpy).toHaveBeenCalledWith("12345");

    expect(screen.getByText("Loading job plots…")).toBeInTheDocument();

    await waitFor(
      () => {
        expect(screen.queryByText("Loading job plots…")).not.toBeInTheDocument();
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
      .mockResolvedValue(minimalPlotsResponse);

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
    vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(
      minimalPlotsResponse
    );

    const { container } = renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByText("Job Detail")).toBeInTheDocument();
    });

    expect(container.querySelector(".col-sm-20")).toBeNull();
    expect(container.querySelector(".col-sm-12")).toBeTruthy();
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
    vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(
      minimalPlotsResponse
    );

    renderJobDetail("12345", { is_staff: true });

    await waitFor(() => {
      expect(screen.getByText("Job Detail")).toBeInTheDocument();
    });
    await userEvent.click(
      screen.getByRole("button", { name: /Job-level Metrics/i })
    );
    expect(
      screen.getByText("No usable PMC telemetry for average CPU frequency")
    ).toBeInTheDocument();
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
    vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(
      minimalPlotsResponse
    );

    renderJobDetail("12345", { is_staff: false });

    await waitFor(() => {
      expect(screen.getByText("Job Detail")).toBeInTheDocument();
    });
    await userEvent.click(
      screen.getByRole("button", { name: /Job-level Metrics/i })
    );
    expect(screen.getAllByText("Data not available.").length).toBeGreaterThan(0);
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
      .mockResolvedValueOnce(minimalPlotsResponse);

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByText("Job Detail")).toBeInTheDocument();
    });
    expect(screen.getByText("Loading job plots…")).toBeInTheDocument();

    await waitFor(() => {
      expect(getJobPlotsSpy).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.queryByText("Loading job plots…")).not.toBeInTheDocument();
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
    vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(
      minimalPlotsResponse
    );

    renderJobDetail("12345");

    await waitFor(() => {
      expect(screen.getByText("Job Detail")).toBeInTheDocument();
    });
    expect(screen.getByText("Total GPUs per Machine:")).toBeInTheDocument();
    expect(screen.getByText("4.00")).toBeInTheDocument();
  });

  it("renders second GPU roofline panel in host-level plots", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(
      minimalPlotsResponse
    );

    renderJobDetail("12345");
    await waitFor(() => {
      expect(document.querySelectorAll(".bokeh-embed-wrapper").length).toBe(4);
    });
  });

  it("shows zoom links for each host-level plot and closes zoom overlay with x", async () => {
    vi.spyOn(apiModule.api, "getJobDetailLight").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobDetail").mockResolvedValue(
      minimalJobDetailResponse
    );
    vi.spyOn(apiModule.api, "getJobPlots").mockResolvedValue(
      minimalPlotsResponse
    );

    renderJobDetail("12345");
    await waitFor(() => {
      expect(screen.getByText("Host-level Plots")).toBeInTheDocument();
    });

    const zoomLinks = screen.getAllByRole("button", { name: /^Zoom /i });
    expect(zoomLinks.length).toBe(4);

    await userEvent.click(screen.getByRole("button", { name: "Zoom Summary plot" }));
    expect(
      screen.getByRole("dialog", { name: "Summary plot zoom view" })
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close zoom window" }));
    expect(
      screen.queryByRole("dialog", { name: "Summary plot zoom view" })
    ).not.toBeInTheDocument();
  });
});
