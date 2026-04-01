import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import JobMonitor from "./JobMonitor";
import * as apiModule from "../api";

describe("JobMonitor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders GPU columns and shows N/A when GPU data is missing", async () => {
    vi.spyOn(apiModule.api, "getJobMonitor").mockResolvedValue({
      window_days: 30,
      results: [
        {
          username: "alice",
          total_jobs: 20,
          failed_jobs: 2,
          failed_rate: 10,
          timedout_jobs: 1,
          timedout_rate: 5,
          gpu_count_total: null,
          gpu_active_total: null,
          gpu_active_percentage: null,
        },
      ],
    });

    render(
      <MemoryRouter>
        <JobMonitor />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("alice")).toBeInTheDocument();
    });

    expect(screen.getByText("Total GPUs Allocated")).toBeInTheDocument();
    expect(screen.getByText("Number of GPUs Active")).toBeInTheDocument();
    expect(screen.getByText("Percentage of GPUs Active")).toBeInTheDocument();
    expect(screen.getAllByText("N/A")).toHaveLength(3);
  });
});
