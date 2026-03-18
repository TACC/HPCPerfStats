import { render, screen, waitFor } from "@testing-library/react";
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
});

