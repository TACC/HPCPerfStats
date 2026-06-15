import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import HistogramThumbnails from "./HistogramThumbnails";
import { SessionContext } from "../session-context";
import { axeSeriousViolations } from "../axe-test-utils";

vi.mock("next/navigation", () => {
  const stableSearchParams = new URLSearchParams();
  return {
    useSearchParams: () => stableSearchParams,
  };
});

vi.mock("../bokehInit", () => ({
  ensureBokehLoaded: vi.fn(() => Promise.resolve(globalThis.window?.Bokeh)),
}));

const VALID_BOKEH_ITEM = {
  doc: {
    root_ids: ["p1001"],
    roots: [{ id: "p1001", type: "object", name: "GridPlot" }],
  },
};

function embedViewsWithIdleDoc() {
  const doc = {
    is_idle: true,
    idle: { connect: vi.fn(), disconnect: vi.fn() },
  };
  return { roots: [{ model: { document: doc } }] };
}

function renderHistograms(ui) {
  return render(
    <SessionContext.Provider
      value={{ logged_in: true, is_staff: false, username: "tester" }}
    >
      {ui}
    </SessionContext.Provider>,
  );
}

describe("HistogramThumbnails", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.Bokeh;
  });

  it("exposes a labelled region and a visible desktop chart title", () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    const embedItem = vi.fn().mockResolvedValue(embedViewsWithIdleDoc());
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderHistograms(
      <HistogramThumbnails
        histograms={[
          {
            title: "Jobs by queue",
            plot_item_thumb: VALID_BOKEH_ITEM,
            plot_item_full: VALID_BOKEH_ITEM,
          },
        ]}
      />,
    );

    expect(
      screen.getByRole("region", { name: "Histogram charts for this job list" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Jobs by queue").length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByRole("button", {
        name: "Jobs by queue: enlarge chart",
      }),
    ).toBeInTheDocument();
  });

  it("embeds full histogram in popover after open (thumb then full Bokeh embed)", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    const embedItem = vi.fn().mockResolvedValue(embedViewsWithIdleDoc());
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderHistograms(
      <HistogramThumbnails
        histograms={[
          {
            title: "Jobs by queue",
            plot_item_thumb: VALID_BOKEH_ITEM,
            plot_item_full: VALID_BOKEH_ITEM,
          },
        ]}
      />,
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalledTimes(1));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Jobs by queue: enlarge chart",
      }),
    );
    await waitFor(() => expect(embedItem).toHaveBeenCalledTimes(2));
  });

  it("closes the popover when Close is clicked", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    const embedItem = vi.fn().mockResolvedValue(embedViewsWithIdleDoc());
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderHistograms(
      <HistogramThumbnails
        histograms={[
          {
            title: "Jobs by queue",
            plot_item_thumb: VALID_BOKEH_ITEM,
            plot_item_full: VALID_BOKEH_ITEM,
          },
        ]}
      />,
    );

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", {
        name: "Jobs by queue: enlarge chart",
      }),
    );
    expect(await screen.findByRole("dialog", { name: /jobs by queue/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close full size view" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /jobs by queue/i })).not.toBeInTheDocument();
    });
  });

  it("renders enlarge control below the plot without overlapping the chart shell", () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    window.Bokeh = { embed: { embed_item: vi.fn().mockResolvedValue(embedViewsWithIdleDoc()) } };

    const { container } = renderHistograms(
      <HistogramThumbnails
        histograms={[
          {
            title: "Jobs by queue",
            plot_item_thumb: VALID_BOKEH_ITEM,
            plot_item_full: VALID_BOKEH_ITEM,
          },
        ]}
      />,
    );

    const enlarge = screen.getByRole("button", { name: "Jobs by queue: enlarge chart" });
    expect(enlarge.closest(".histogram-thumbnail-actions")).toBeTruthy();
    expect(enlarge.closest(".histogram-thumbnail-shell")).toBeNull();
    expect(container.querySelector(".histogram-thumbnail-enlarge")).not.toHaveStyle({
      position: "absolute",
    });

    const shell = container.querySelector(".histogram-thumbnail-shell");
    const card = container.querySelector(".histogram-thumbnail-card");
    expect(shell).toBeTruthy();
    expect(card).toBeTruthy();
    expect(shell.style.width).toBe("");
    expect(shell).toHaveClass("histogram-thumbnail-shell");
    expect(shell).toHaveClass("h-[200px]");
    expect(shell).toHaveClass("w-[280px]");
  });

  it("does not show zero-size embed failure in the thumbnail shell", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    const embedItem = vi.fn().mockResolvedValue(embedViewsWithIdleDoc());
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderHistograms(
      <HistogramThumbnails
        embedAllowed
        histograms={[
          {
            title: "Jobs by queue",
            plot_item_thumb: VALID_BOKEH_ITEM,
            plot_item_full: VALID_BOKEH_ITEM,
          },
        ]}
      />,
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalled());
    expect(
      screen.queryByText(/chart container stayed at zero size/i),
    ).not.toBeInTheDocument();
  });

  it("uses non-modal enlarge dialog so the page stays interactable", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    const embedItem = vi.fn().mockResolvedValue(embedViewsWithIdleDoc());
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderHistograms(
      <HistogramThumbnails
        histograms={[
          {
            title: "Jobs by queue",
            plot_item_thumb: VALID_BOKEH_ITEM,
            plot_item_full: VALID_BOKEH_ITEM,
          },
        ]}
      />,
    );

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", {
        name: "Jobs by queue: enlarge chart",
      }),
    );

    expect(await screen.findByTestId("histogram-enlarge-dialog")).toBeInTheDocument();
    expect(document.documentElement).not.toHaveAttribute("data-scroll-locked");
  });

  it("has no serious axe violations for thumbnail and open popover", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    const embedItem = vi.fn().mockResolvedValue(undefined);
    window.Bokeh = { embed: { embed_item: embedItem } };

    const item = { doc: {}, root_ids: ["r1"] };
    const view = renderHistograms(
      <HistogramThumbnails
        histograms={[
          {
            title: "Jobs by queue",
            plot_item_thumb: item,
            plot_item_full: item,
          },
        ]}
      />,
    );

    expect(await axeSeriousViolations(view.container)).toEqual([]);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Jobs by queue: enlarge chart",
      }),
    );

    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /jobs by queue/i })).toBeInTheDocument();
    });
  });
});
