import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import JobList from "./JobList";
import * as apiModule from "../api";

function renderJobList(initialEntries = ["/jobs"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/jobs" element={<JobList />} />
      </Routes>
    </MemoryRouter>
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
    expect(screen.getByText("job1")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
  });

  it("shows histogram unavailable details and supports copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
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

    const detailsTriggers = screen.getAllByLabelText("Show plot error details");
    fireEvent.mouseEnter(detailsTriggers[0]);

    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "No queue histogram data available for this query."
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Copy Error Detail" })[0]);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(
        "No queue histogram data available for this query."
      );
    });
  });

  it("keeps error detail and copy controls visible in mobile histogram view", async () => {
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

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
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
    expect(screen.getByText("Error Detail")).toBeInTheDocument();

    const detailsTrigger = screen.getByLabelText("Show plot error details");
    fireEvent.mouseEnter(detailsTrigger);
    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "No queue histogram data available for this query."
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy Error Detail" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(
        "No queue histogram data available for this query."
      );
    });
  });
});

