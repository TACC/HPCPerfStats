import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PageClusterDashboard from "../PageClusterDashboard";
import { PubDashboardBundleContext } from "../../pub-dashboard-bundle-context";

vi.mock("../../components/BokehEmbed", () => ({
  default: function BokehEmbedStub({ item, id }) {
    if (!item) return null;
    return (
      <div data-testid={id === undefined ? "bokeh-no-id" : `bokeh-${id}`}>
        bokeh stub
      </div>
    );
  },
}));

const mockBokeh = {
  doc: { roots: [], title: "", version: "3.0.0" },
  root_id: "r1",
  target_id: "t1",
  version: "3.0.0",
};

const mockReadyBundle = {
  status: "ready",
  schema_version: 2,
  machine_name: "cluster.test",
  sections: {
    expansion_factor: {
      monthly_daily_histograms: {
        "2100-02": {
          expansion_factor_definition: "def-new",
          histogram_bin_edges: [0, 0.5, 1.0],
          histogram_counts: [2, 1, 0],
          bokeh_histogram_json_item: mockBokeh,
        },
        "2099-01": {
          expansion_factor_definition: "def",
          histogram_bin_edges: [0, 0.5, 1.0],
          histogram_counts: [0, 0, 0],
          bokeh_histogram_json_item: mockBokeh,
        },
      },
      yearly_weekly_histograms: {
        "2099": {
          expansion_factor_definition: "def-y-new",
          histogram_bin_edges: [0, 1],
          histogram_counts: [2],
          bokeh_histogram_json_item: mockBokeh,
        },
        "2098": {
          expansion_factor_definition: "def-y",
          histogram_bin_edges: [0, 1],
          histogram_counts: [1],
          bokeh_histogram_json_item: mockBokeh,
        },
      },
    },
  },
};

function renderWithPubContext(value) {
  return render(
    <PubDashboardBundleContext.Provider value={value}>
      <PageClusterDashboard />
    </PubDashboardBundleContext.Provider>,
  );
}

describe("PageClusterDashboard", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lazy-mounts yearly histograms first; monthly after user switches grouping", async () => {
    const user = userEvent.setup();
    renderWithPubContext({
      loading: false,
      bundle: mockReadyBundle,
      error: null,
    });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: "Dashboard" }),
      ).toBeInTheDocument();
    });

    expect(screen.getByTestId("bokeh-pub-expansion-factor-year-2098")).toBeInTheDocument();
    expect(screen.getByTestId("bokeh-pub-expansion-factor-year-2099")).toBeInTheDocument();
    expect(screen.queryByTestId("bokeh-pub-expansion-factor-month-2099-01")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bokeh-pub-expansion-factor-month-2100-02")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Monthly" }));

    await waitFor(() => {
      expect(screen.getByTestId("bokeh-pub-expansion-factor-month-2100-02")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("bokeh-pub-expansion-factor-year-2098")).not.toBeInTheDocument();
  });

  it("shows loading state while bundle is pending", () => {
    renderWithPubContext({
      loading: true,
      bundle: null,
      error: null,
    });
    expect(screen.getByText("Loading cluster dashboard…")).toBeInTheDocument();
  });
});
