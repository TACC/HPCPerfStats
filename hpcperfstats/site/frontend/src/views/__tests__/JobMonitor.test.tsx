import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import JobMonitor from "../JobMonitor";
import { useJobMonitorQuery } from "@/hooks/use-job-monitor";
import { jobMonitorGpuRetrieve } from "@/api/generated/monitor/monitor";
import { renderWithProviders } from "@/test-utils/render-with-providers";

vi.mock("@/hooks/use-job-monitor", () => ({
  useJobMonitorQuery: vi.fn(),
}));

vi.mock("@/api/generated/monitor/monitor", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/generated/monitor/monitor")>();
  return {
    ...actual,
    jobMonitorGpuRetrieve: vi.fn(),
  };
});

function setJobMonitorQueryMock(
  overrides: Partial<ReturnType<typeof useJobMonitorQuery>> = {},
) {
  vi.mocked(useJobMonitorQuery).mockReturnValue({
    data: null,
    error: null,
    loading: false,
    fetching: false,
    refetch: vi.fn(),
    ...overrides,
  });
}

describe("JobMonitor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.mocked(useJobMonitorQuery).mockReset();
    vi.mocked(jobMonitorGpuRetrieve).mockReset();
  });

  it("renders GPU columns and shows N/A when GPU data is missing", async () => {
    setJobMonitorQueryMock({
      data: {
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
      },
    });
    vi.mocked(jobMonitorGpuRetrieve).mockResolvedValue({
      username: "alice",
      gpu_count_total: null,
      gpu_active_total: null,
      gpu_active_percentage: null,
      has_data: false,
    });

    renderWithProviders(<JobMonitor />);

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
    setJobMonitorQueryMock({
      data: {
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
      },
    });
    vi.mocked(jobMonitorGpuRetrieve).mockResolvedValue({
      username: "bob",
      gpu_count_total: 8,
      gpu_active_total: 4,
      gpu_active_percentage: 50,
      has_data: true,
    });

    renderWithProviders(<JobMonitor />);

    await waitFor(() => {
      expect(screen.getByText("bob")).toBeInTheDocument();
    });
    const row = await waitFor(() => {
      const link = screen.getByRole("link", { name: "bob" });
      return link.closest("tr");
    });
    expect(within(row!).getByText("8.00")).toBeInTheDocument();
    expect(within(row!).getByText("50.00%")).toBeInTheDocument();
    expect(within(row!).getAllByText("4.00").length).toBeGreaterThanOrEqual(1);
  });

  it("shows an empty-state row when no users match the monitor window", async () => {
    setJobMonitorQueryMock({
      data: {
        window_days: 30,
        results: [],
      },
    });

    renderWithProviders(<JobMonitor />);

    await waitFor(() => {
      expect(
        screen.getByText(/no jobs found in the selected time window/i),
      ).toBeInTheDocument();
    });
  });

  it("shows an error banner when the monitor API fails", async () => {
    setJobMonitorQueryMock({
      error: "Monitor API down",
    });

    renderWithProviders(<JobMonitor />);

    await waitFor(() => {
      expect(
        screen.getByText(/error loading job monitor data: unable to load job monitor data/i),
      ).toBeInTheDocument();
    });
  });
});
