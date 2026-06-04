import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { axeSeriousViolations } from "../axe-test-utils";
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

    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    const view = renderJobList();

    await waitFor(() => {
      expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
    });
    expect(await axeSeriousViolations(view.container)).toEqual([]);
    expect(screen.getByRole("heading", { name: /distributions for this job selection/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /jump to histograms/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /continue to job table/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Performance data/i })).toBeInTheDocument();
    const tableHeaders = within(screen.getByRole("table")).getAllByRole("columnheader");
    expect(tableHeaders[tableHeaders.length - 1].textContent.trim()).toBe("name");
    expect(screen.getByText("job1")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(screen.getByText("Summary available")).toBeInTheDocument();
  });

  it("shows Sample Count as the second column for staff users", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [
        {
          jid: 1,
          sample_count: 1234,
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
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList(["/jobs"], { is_staff: true });

    await waitFor(() => {
      expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
    });
    const tableHeaders = within(screen.getByRole("table")).getAllByRole("columnheader");
    expect(tableHeaders[1].textContent.trim()).toBe("Sample count");
    expect(screen.getByText("1,234.00")).toBeInTheDocument();
  });

  it("shows mean queue wait for staff when aggregates include it", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [
        {
          jid: 1,
          sample_count: 10,
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
      aggregates: {
        total_node_hours: 64,
        queue_wait_mean_hours: 1.25,
      },
      qname: "Jobs",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 1 },
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList(["/jobs"], { is_staff: true });

    await waitFor(() => {
      expect(screen.getByText(/Mean queue wait \(all matching jobs\):/)).toBeInTheDocument();
    });
    expect(screen.getByText(/1\.25 hours/)).toBeInTheDocument();
  });

  it("does not show queue wait summary lines for non-staff even if aggregates would include them", async () => {
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
      aggregates: {
        total_node_hours: 64,
        queue_wait_mean_hours: 1.25,
      },
      qname: "Jobs",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 1 },
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList(["/jobs"], { is_staff: false });

    await waitFor(() => {
      expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Mean queue wait \(all matching jobs\):/)).not.toBeInTheDocument();
  });

  it("hides Sample Count column for non-staff users", async () => {
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
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList(["/jobs"], { is_staff: false });

    await waitFor(() => {
      expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
    });
    expect(screen.queryByRole("columnheader", { name: "Sample Count" })).not.toBeInTheDocument();
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
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockImplementation((_, metric) => {
      if (metric === "runtime") {
        return Promise.resolve({
          metric: "runtime",
          title: "Runtime",
          plot_item_thumb: { doc: {}, root_ids: ["thumb-root"] },
          plot_item_full: { doc: {}, root_ids: ["full-root"] },
          plot_unavailable_reason: null,
        });
      }
      return Promise.resolve(null);
    });

    renderJobList();

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /^jobs$/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /^charts$/i })).toBeInTheDocument();

    const distSection = document.getElementById("job-list-distributions");
    expect(distSection).toBeTruthy();
    expect(distSection).toHaveAttribute("hidden");

    await waitFor(() => {
      expect(document.querySelector(".histogram-thumbnails-grid")).toBeNull();
    });

    fireEvent.click(screen.getByRole("link", { name: /jump to histograms/i }));

    await waitFor(() => {
      expect(distSection).not.toHaveAttribute("hidden");
    });
    expect(screen.getByRole("tab", { name: /^charts$/i })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => {
      expect(document.querySelector(".histogram-thumbnails-grid")).toBeTruthy();
    });
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
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList(["/year/2024"]);

    await waitFor(() => {
      expect(screen.getByText(/calendar year 2024/i)).toBeInTheDocument();
    });
  });

  it("renders pagination controls at the top and the bottom when multiple pages exist", async () => {
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
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();

    await waitFor(() => {
      expect(
        screen.getByRole("navigation", { name: /Job list pagination \(top\)/i }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("navigation", { name: /Job list pagination \(bottom\)/i }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("First")).toHaveLength(2);
    expect(screen.getAllByText("Last")).toHaveLength(2);
  });

  it("does not render any pagination control when only a single page exists", async () => {
    vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
      job_list: [],
      nj: 0,
      aggregates: {},
      qname: "Jobs",
      order_by: "-end_time",
      pagination: { page: 1, num_pages: 1 },
    });
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();

    await waitFor(() => {
      expect(screen.getByText("Jobs = 0")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("navigation", { name: /Job list pagination/i }),
    ).not.toBeInTheDocument();
  });

  describe("default first-click sort direction", () => {
    function jobRow(extra = {}) {
      return {
        jid: 1,
        sample_count: 1234,
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
        ...extra,
      };
    }

    function mockJobListWithOrderBy(orderBy) {
      vi.spyOn(apiModule.api, "getJobList").mockResolvedValue({
        job_list: [jobRow()],
        nj: 1,
        aggregates: { total_node_hours: 64 },
        qname: "Jobs",
        order_by: orderBy,
        pagination: { page: 1, num_pages: 1 },
      });
      vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);
    }

    function getOrderByFromHref(link) {
      const href = link.getAttribute("href") || "";
      const query = href.includes("?") ? href.split("?")[1] : "";
      return new URLSearchParams(query).get("order_by");
    }

    it("first click on Sample Count sorts descending (largest first)", async () => {
      mockJobListWithOrderBy("-end_time");
      renderJobList(["/jobs"], { is_staff: true });

      await waitFor(() => {
        expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
      });

      const link = screen.getByRole("link", { name: /Sample Count/i });
      expect(getOrderByFromHref(link)).toBe("-sample_count");
    });

    it("first click on Performance Data sorts so Summary available is first (ascending)", async () => {
      mockJobListWithOrderBy("-end_time");
      renderJobList(["/jobs"], { is_staff: false });

      await waitFor(() => {
        expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
      });

      const link = screen.getByRole("link", { name: /Performance Data/i });
      expect(getOrderByFromHref(link)).toBe("performance_sort_rank");
    });

    it("toggles Performance Data direction when already sorted ascending", async () => {
      mockJobListWithOrderBy("performance_sort_rank");
      renderJobList(["/jobs?order_by=performance_sort_rank"], { is_staff: false });

      await waitFor(() => {
        expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
      });

      const link = screen.getByRole("link", { name: /Performance Data/i });
      expect(getOrderByFromHref(link)).toBe("-performance_sort_rank");
    });

    it("toggles Sample Count direction when already sorted descending", async () => {
      mockJobListWithOrderBy("-sample_count");
      renderJobList(["/jobs?order_by=-sample_count"], { is_staff: true });

      await waitFor(() => {
        expect(screen.getByText("Jobs = 1")).toBeInTheDocument();
      });

      const link = screen.getByRole("link", { name: /Sample Count/i });
      expect(getOrderByFromHref(link)).toBe("sample_count");
    });
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
      expect(screen.getByText("Jobs = 0")).toBeInTheDocument();
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
    vi.spyOn(apiModule.api, "getJobMetricHistogram").mockResolvedValue(null);

    renderJobList();

    await waitFor(() => {
      expect(screen.getByText("Jobs = 0")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: "Show plot error details" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy error detail" })).not.toBeInTheDocument();
  });
});

