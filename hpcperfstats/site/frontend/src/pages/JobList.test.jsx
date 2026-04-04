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
          <Route path="/year/:year" element={<JobList />} />
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
      value: vi.fn().mockImplementation((query) => ({
        matches: true,
        media: query,
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
    expect(screen.getByRole("status", { name: /loading job list/i })).toBeInTheDocument();
  });

  it("renders basic job list data", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [
        {
          jid: 1,
          performance: {
            label: "Summary available",
            tone: "success",
            aria_label: "Performance: Summary available",
            sort_rank: 0,
          },
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
    expect(screen.getByRole("heading", { name: /distributions for this list/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /jump to distributions/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to job table/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Performance Data" })).toBeInTheDocument();
    expect(screen.getByText("job1")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(screen.getByText("Summary available")).toBeInTheDocument();
  });

  it("on narrow viewports uses Jobs and Charts tabs and jump link opens Charts", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: query.includes("min-width: 992px") ? false : true,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [
        {
          jid: 1,
          performance: {
            label: "Summary available",
            tone: "success",
            aria_label: "Performance: Summary available",
            sort_rank: 0,
          },
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
    vi.spyOn(apiModule.api, "getJobQueueHistograms").mockResolvedValue({ plots: [] });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /^jobs$/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /^charts$/i })).toBeInTheDocument();

    const distSection = document.getElementById("job-list-distributions");
    expect(distSection).toBeTruthy();
    expect(distSection).toHaveAttribute("hidden");

    fireEvent.click(screen.getByRole("link", { name: /jump to distributions/i }));

    await waitFor(() => {
      expect(distSection).not.toHaveAttribute("hidden");
    });
    expect(screen.getByRole("tab", { name: /^charts$/i })).toHaveAttribute("aria-selected", "true");
  });

  it("shows human summary for year route", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [],
      nj: 0,
      aggregates: {},
      qname: "Jobs in year 2024",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 1 },
    });
    vi.spyOn(apiModule.api, "getJobQueueHistograms").mockResolvedValue({ plots: [] });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList(["/year/2024"]);

    await waitFor(() => {
      expect(screen.getByText(/calendar year 2024/i)).toBeInTheDocument();
    });
  });

  it("renders pagination controls when multiple pages exist", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [
        {
          jid: 1,
          performance: {
            label: "Summary available",
            tone: "success",
            aria_label: "Performance: Summary available",
            sort_rank: 0,
          },
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
      nj: 100,
      aggregates: { total_node_hours: 6400 },
      qname: "Jobs",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 5 },
    });
    vi.spyOn(apiModule.api, "getJobQueueHistograms").mockResolvedValue({
      plots: [],
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();

    await waitFor(() => {
      expect(screen.getByText("First")).toBeInTheDocument();
    });
    expect(screen.getByText("Last")).toBeInTheDocument();
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
    expect(
      screen.getAllByText("Unavailable — Data not available.").length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: "Show plot error details" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy error detail" })).not.toBeInTheDocument();
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
    expect(
      screen.queryByRole("button", { name: "Show plot error details" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy error detail" })).not.toBeInTheDocument();
  });
});

