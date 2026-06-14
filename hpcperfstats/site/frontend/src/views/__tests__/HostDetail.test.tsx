import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HostDetail from "../HostDetail";
import { useHostPlotQuery } from "@/hooks/use-host-plot";
import { renderWithProviders } from "@/test-utils/render-with-providers";
import { VALID_BOKEH_JSON_ITEM } from "@/test-utils/bokeh-fixtures";

vi.mock("@/hooks/use-host-plot", () => ({
  useHostPlotQuery: vi.fn(),
}));

vi.mock("../bokehInit", () => ({
  ensureBokehLoaded: vi.fn(() => Promise.resolve(globalThis.window?.Bokeh)),
}));

function setHostPlotQueryMock(
  overrides: Partial<ReturnType<typeof useHostPlotQuery>> = {},
) {
  vi.mocked(useHostPlotQuery).mockReturnValue({
    data: null,
    error: null,
    loading: false,
    ...overrides,
  });
}

function renderHostDetail(path = "/host/node1.cluster/plot", session = { is_staff: false }) {
  return renderWithProviders(<HostDetail />, { session, initialPath: path });
}

describe("HostDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.mocked(useHostPlotQuery).mockReset();
    delete window.Bokeh;
  });

  it("shows loading while the host plot request is in flight", () => {
    setHostPlotQueryMock({ loading: true });
    renderHostDetail();
    expect(screen.getByText(/loading host plot/i)).toBeInTheDocument();
  });

  it("shows plot unavailable message without details for non-staff", async () => {
    setHostPlotQueryMock({
      data: {
        host: "node1.cluster",
        plot_item: null,
        plot_unavailable_reason:
          "No host plot data available for this host/time range.",
        end_time__gte: "2024-01-01T00:00:00Z",
        end_time__lte: "2024-01-02T00:00:00Z",
      },
    });

    renderHostDetail();

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /node1\.cluster utilization/i }),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Unavailable — Data not available.")).toBeInTheDocument();

    expect(screen.queryByLabelText("Show plot error details")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy error detail" })).not.toBeInTheDocument();
  });

  it("shows staff plot error detail controls when the plot is unavailable", async () => {
    setHostPlotQueryMock({
      data: {
        host: "node1.cluster",
        plot_item: null,
        plot_unavailable_reason: "No host plot data available for this host/time range.",
        end_time__gte: "2024-01-01T00:00:00Z",
        end_time__lte: "2024-01-02T00:00:00Z",
      },
    });

    renderHostDetail("/host/node1.cluster/plot", { is_staff: true });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /node1\.cluster utilization/i }),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Show plot error details" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy error detail" })).toBeInTheDocument();
  });

  it("embeds the host plot when plot_item is returned", async () => {
    const embedItem = vi.fn(() => ({
      roots: [
        {
          model: {
            document: {
              is_idle: true,
              idle: { connect: vi.fn(), disconnect: vi.fn() },
            },
          },
        },
      ],
    }));
    window.Bokeh = { embed: { embed_item: embedItem } };

    setHostPlotQueryMock({
      data: {
        host: "node1.cluster",
        plot_item: VALID_BOKEH_JSON_ITEM,
        plot_unavailable_reason: null,
        end_time__gte: "2024-01-01T00:00:00Z",
        end_time__lte: "2024-01-02T00:00:00Z",
      },
    });

    renderHostDetail();

    await waitFor(() => {
      expect(embedItem).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole("link", { name: /view jobs that ran on this host/i })).toHaveAttribute(
      "href",
      "/machine/host/node1.cluster",
    );
  });

  it("shows a banner when the API request fails", async () => {
    setHostPlotQueryMock({ error: "Host plot API failed" });
    renderHostDetail();
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/host plot api failed/i);
    });
  });
});
