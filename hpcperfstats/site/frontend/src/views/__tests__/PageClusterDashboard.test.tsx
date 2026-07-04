import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PageClusterDashboard from "../PageClusterDashboard";

vi.mock("@/hooks/use-pub-dashboard", () => ({
  usePubDashboard: vi.fn(),
}));

vi.mock("@/components/LazyExpansionHistogram", () => ({
  default: function LazyExpansionHistogramStub({ periodKey }: { periodKey: string }) {
    return <div>{periodKey}</div>;
  },
}));

import { usePubDashboard } from "@/hooks/use-pub-dashboard";
import { parseApiResponse } from "@/api/parse-api-response";

const mockReadyBundle = parseApiResponse("GET", "/api/pub/cluster-dashboard/", {
  status: "ready",
  schema_version: 2,
  machine_name: "cluster.test",
  sections: {
    expansion_factor: {
      yearly_period_keys: ["2099", "2098"],
      monthly_period_keys: ["2100-02", "2099-01"],
    },
  },
});

describe("PageClusterDashboard", () => {
  beforeEach(() => {
    vi.mocked(usePubDashboard).mockReturnValue({
      loading: false,
      initialLoading: false,
      refetchBusy: false,
      bundle: mockReadyBundle,
      error: null,
      refetch: vi.fn(),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders yearly period keys first; monthly keys after grouping switch", async () => {
    const user = userEvent.setup();
    render(<PageClusterDashboard />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: "Dashboard" }),
      ).toBeInTheDocument();
    });

    expect(screen.getByText("2098")).toBeInTheDocument();
    expect(screen.getByText("2099")).toBeInTheDocument();
    expect(screen.queryByText("2100-02")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Monthly" }));

    await waitFor(() => {
      expect(screen.getByText("2100-02")).toBeInTheDocument();
    });
    expect(screen.queryByText("2098")).not.toBeInTheDocument();
  });

  it("shows loading state while bundle is pending", () => {
    vi.mocked(usePubDashboard).mockReturnValue({
      loading: true,
      initialLoading: true,
      refetchBusy: false,
      bundle: null,
      error: null,
      refetch: vi.fn(),
    });
    render(<PageClusterDashboard />);
    expect(screen.getByText("Loading cluster dashboard…")).toBeInTheDocument();
  });

  it("keeps dashboard interactive during refetch", async () => {
    vi.mocked(usePubDashboard).mockReturnValue({
      loading: false,
      initialLoading: false,
      refetchBusy: true,
      bundle: mockReadyBundle,
      error: null,
      refetch: vi.fn(),
    });
    render(<PageClusterDashboard />);

    await waitFor(() => {
      expect(screen.getByText("2099")).toBeInTheDocument();
    });
    expect(screen.getByText("Updating dashboard…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Monthly" })).toBeEnabled();
  });
});
