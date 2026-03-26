import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import JobList from "./JobList";
import * as apiModule from "../api";
import { SessionContext } from "../session-context";

function renderJobList(initialEntries = ["/jobs"], session = { is_staff: false }) {
  return render(
    <SessionContext.Provider value={session}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/jobs" element={<JobList />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>
  );
}

describe("JobList", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        media: "",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it("shows loading indicator while fetching", () => {
    vi.spyOn(apiModule.api, "getJobList").mockReturnValue(
      new Promise(() => {})
    );
    vi.spyOn(apiModule.api, "getJobQueueHistograms").mockResolvedValue({
      plots: [],
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();
    expect(
      screen.getByText("Loading job list…")
    ).toBeInTheDocument();
  });

  it("renders basic job list data", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [
        {
          jid: 1,
          has_metrics: true,
          username: "alice",
          account: "acct",
          start_time: "2024-01-01T00:00:00Z",
          end_time: "2024-01-01T01:00:00Z",
          runtime: 3600,
          queue: "normal",
          jobname: "job1",
          state: "COMPLETED",
          ncores: 32,
          nhosts: 2,
          node_hrs: 64,
        },
      ],
      nj: 1,
      aggregates: { total_node_hours: 64 },
      qname: "Jobs",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 1 },
    });

    vi.spyOn(apiModule.api, "getJobQueueHistograms").mockResolvedValue({
      plots: [],
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();

    await waitFor(() => {
      expect(screen.getByText("#Jobs = 1")).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "Performance Data" })).toBeInTheDocument();
    expect(screen.getByText("job1")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
  });

  it("hides histogram unavailable details and copy for non-staff users", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [],
      nj: 0,
      aggregates: {},
      qname: "Jobs",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 1 },
    });
    vi.spyOn(apiModule.api, "getJobQueueHistograms").mockResolvedValue({
      plots: [
        {
          key: "jobs_by_queue",
          title: "Jobs by queue",
          plot_item_thumb: null,
          plot_item_full: null,
          plot_unavailable_reason:
            "No queue histogram data available for this query.",
        },
      ],
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue({
      metric: "runtime",
      title: "Runtime",
      plot_item_thumb: null,
      plot_item_full: null,
      plot_unavailable_reason:
        "No histogram data available for metric 'runtime' in this query.",
    });

    renderJobList();

    await waitFor(() => {
      expect(screen.getByText("#Jobs = 0")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Plot not available").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Show plot error details")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy Error Detail" })).not.toBeInTheDocument();
  });

  it("hides error detail and copy controls in mobile histogram view for non-staff", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: true,
        media: "",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [],
      nj: 0,
      aggregates: {},
      qname: "Jobs",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 1 },
    });
    vi.spyOn(apiModule.api, "getJobQueueHistograms").mockResolvedValue({
      plots: [
        {
          key: "jobs_by_queue",
          title: "Jobs by queue",
          plot_item_thumb: null,
          plot_item_full: null,
          plot_unavailable_reason:
            "No queue histogram data available for this query.",
        },
      ],
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();

    await waitFor(() => {
      expect(screen.getByText("#Jobs = 0")).toBeInTheDocument();
    });
    expect(screen.queryByText("Error Detail")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Show plot error details")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy Error Detail" })).not.toBeInTheDocument();
  });
});

