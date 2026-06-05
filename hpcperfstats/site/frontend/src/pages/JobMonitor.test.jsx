import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
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
        },
      ],
    });
    vi.spyOn(apiModule.api, "getJobMonitorGpuForUser").mockResolvedValue({
      username: "alice",
      gpu_count_total: null,
      gpu_active_total: null,
      gpu_active_percentage: null,
      has_data: false,
    });

    render(
      <MemoryRouter>
        <JobMonitor />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("alice")).toBeInTheDocument();
    });

    expect(screen.getByText("Total GPUs Allocated")).toBeInTheDocument();
    expect(screen.getByText("Number of GPUs Active")).toBeInTheDocument();
    expect(screen.getByText("Percentage of GPUs Active")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText("N/A")).toHaveLength(3);
    });
  });

  it("renders GPU summary values when per-user GPU data is available", async () => {
    vi.spyOn(apiModule.api, "getJobMonitor").mockResolvedValue({
      window_days: 14,
      results: [
        {
          username: "bob",
          total_jobs: 40,
          failed_jobs: 4,
          failed_rate: 10,
          timedout_jobs: 2,
          timedout_rate: 5,
        },
      ],
    });
    vi.spyOn(apiModule.api, "getJobMonitorGpuForUser").mockResolvedValue({
      username: "bob",
      gpu_count_total: 8,
      gpu_active_total: 4,
      gpu_active_percentage: 50,
      has_data: true,
    });

    render(
      <MemoryRouter>
        <JobMonitor />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("bob")).toBeInTheDocument();
    });
    const row = await waitFor(() => {
      const link = screen.getByRole("link", { name: "bob" });
      return link.closest("tr");
    });
    expect(within(row).getByText("8.00")).toBeInTheDocument();
    expect(within(row).getByText("50.00%")).toBeInTheDocument();
    expect(within(row).getAllByText("4.00").length).toBeGreaterThanOrEqual(1);
  });

  it("shows an empty-state row when no users match the monitor window", async () => {
    vi.spyOn(apiModule.api, "getJobMonitor").mockResolvedValue({
      window_days: 30,
      results: [],
    });

    render(
      <MemoryRouter>
        <JobMonitor />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(
        screen.getByText(/no jobs found in the selected time window/i),
      ).toBeInTheDocument();
    });
  });

  it("shows an error banner when the monitor API fails", async () => {
    vi.spyOn(apiModule.api, "getJobMonitor").mockRejectedValue(new Error("Monitor API down"));

    render(
      <MemoryRouter>
        <JobMonitor />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/error loading job monitor data: monitor api down/i)).toBeInTheDocument();
    });
  });
});
