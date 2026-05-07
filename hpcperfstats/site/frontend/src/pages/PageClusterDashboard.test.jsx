import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PageClusterDashboard from "./PageClusterDashboard.jsx";

vi.mock("../components/BokehEmbed.jsx", () => ({
  default: function BokehEmbedStub({ item, id }) {
    if (!item) return null;
    return (
      <div data-testid={id === undefined ? "bokeh-no-id" : `bokeh-${id}`}>
        bokeh stub
      </div>
    );
  },
}));

describe("PageClusterDashboard", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: "ready",
        schema_version: 2,
        sections: {
          expansion_factor: {
            monthly_daily_histograms: {
              "2099-01": {
                expansion_factor_definition: "def",
                histogram_bin_edges: [0, 0.5, 1.0],
                histogram_counts: [0, 0, 0],
                bokeh_histogram_json_item: {
                  doc: { roots: [], title: "", version: "3.0.0" },
                  root_id: "r1",
                  target_id: "t1",
                  version: "3.0.0",
                },
              },
            },
            yearly_weekly_histograms: {},
          },
        },
      }),
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("loads anonymous dashboard bundle without session APIs", async () => {
    render(
      <MemoryRouter initialEntries={["/cluster-dashboard"]}>
        <Routes>
          <Route path="cluster-dashboard" element={<PageClusterDashboard />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: "Cluster dashboard" }),
      ).toBeInTheDocument();
    });

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/pub/cluster-dashboard/",
      expect.objectContaining({
        method: "GET",
        credentials: "omit",
      }),
    );
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 2, name: "Expansion factor" }),
      ).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByTestId("bokeh-pub-ef-month-2099-01")).toBeInTheDocument();
    });
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
  });
});
