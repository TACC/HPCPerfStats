import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import JobMonitor from "../JobMonitor";
import { useJobMonitorQuery } from "@/hooks/use-job-monitor";
import { jobMonitorGpuRetrieve } from "@/api/generated/monitor/monitor";
import { orvalOkEnvelope } from "@/api/orval-response";
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
    initialLoading: false,
    tableBusy: false,
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

  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
      configurable: true,
      value: 480,
    });
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      value: 1024,
    });
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
      expect(screen.getByRole("link", { name: "alice" })).toBeInTheDocument();
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
    vi.mocked(jobMonitorGpuRetrieve).mockResolvedValue(
      orvalOkEnvelope({
      username: "bob",
      gpu_count_total: 8,
      gpu_active_total: 4,
      gpu_active_percentage: 50,
      has_data: true,
      }),
    );

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
        screen.getByText(/error loading job monitor data: monitor api down/i),
      ).toBeInTheDocument();
    });
  });

  it("loads GPU data with one API call per user (bounded fan-out)", async () => {
    setJobMonitorQueryMock({
      data: {
        window_days: 30,
        results: [
          { username: "alice", total_jobs: 10, failed_jobs: 1, failed_rate: 10 },
          { username: "bob", total_jobs: 20, failed_jobs: 2, failed_rate: 10 },
          { username: "carol", total_jobs: 30, failed_jobs: 3, failed_rate: 10 },
        ],
      },
    });
    vi.mocked(jobMonitorGpuRetrieve).mockResolvedValue({
      has_data: false,
    });

    renderWithProviders(<JobMonitor />);

    await waitFor(() => {
      expect(jobMonitorGpuRetrieve.mock.calls.length).toBeGreaterThanOrEqual(3);
    });
    const usernames = jobMonitorGpuRetrieve.mock.calls.map((call) => call[0]?.username);
    expect(usernames).toEqual(expect.arrayContaining(["alice", "bob", "carol"]));
  });

  it("keeps sort headers available while tableBusy", async () => {
    setJobMonitorQueryMock({
      tableBusy: true,
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
      has_data: false,
    });

    renderWithProviders(<JobMonitor />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /User/i })).toBeInTheDocument();
    });
    const tableWrapper = screen.getByRole("button", { name: /User/i }).closest("[aria-busy]");
    expect(tableWrapper).toHaveAttribute("aria-busy", "true");
    expect(tableWrapper?.className).not.toContain("pointer-events-none");
  });
});
