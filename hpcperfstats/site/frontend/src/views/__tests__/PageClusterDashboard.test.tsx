import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

  it("renders expansion factors tab with yearly before monthly and Bokeh stubs", async () => {
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

    expect(global.fetch).not.toHaveBeenCalled();

    await waitFor(() => {
      expect(
        screen.getByRole("tab", { name: /expansion factors/i }),
      ).toBeInTheDocument();
    });

    const yearly = screen.getByRole("heading", { level: 3, name: "Yearly" });
    const monthly = screen.getByRole("heading", { level: 3, name: "Monthly" });
    expect(
      yearly.compareDocumentPosition(monthly) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      monthly.compareDocumentPosition(yearly) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();

    expect(screen.getByRole("link", { name: "Monthly" })).toHaveAttribute(
      "href",
      "#pub-dashboard-monthly",
    );
    expect(screen.getByRole("link", { name: "Monthly" })).toHaveClass("text-xl");
    expect(screen.getByRole("link", { name: "Monthly" })).not.toHaveClass("text-sm");
    expect(screen.getByRole("link", { name: "Yearly" })).toHaveAttribute(
      "href",
      "#pub-dashboard-yearly",
    );
    expect(screen.getByRole("link", { name: "Yearly" })).toHaveClass("text-xl");
    expect(screen.getByRole("link", { name: "Yearly" })).not.toHaveClass("text-sm");
    expect(
      screen.queryByText(
        "Public cluster dashboards built from pre-warmed aggregates (no live heavy queries).",
      ),
    ).not.toBeInTheDocument();

    const yearKey2099 = screen.getByText("2099");
    const yearKey2098 = screen.getByText("2098");
    expect(
      yearKey2099.compareDocumentPosition(yearKey2098) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    const monthKey210002 = screen.getByText("2100-02");
    const monthKey209901 = screen.getByText("2099-01");
    expect(
      monthKey210002.compareDocumentPosition(monthKey209901) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByTestId("bokeh-pub-expansion-factor-year-2098")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId("bokeh-pub-expansion-factor-year-2099")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId("bokeh-pub-expansion-factor-month-2099-01")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId("bokeh-pub-expansion-factor-month-2100-02")).toBeInTheDocument();
    });
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
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
