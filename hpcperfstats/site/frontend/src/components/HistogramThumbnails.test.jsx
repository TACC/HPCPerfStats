import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HistogramThumbnails from "./HistogramThumbnails";
import { SessionContext } from "../session-context";
import { axeSeriousViolations } from "../axe-test-utils";

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

    const embedItem = vi.fn().mockResolvedValue(undefined);
    window.Bokeh = { embed: { embed_item: embedItem } };

    const item = { doc: {}, root_ids: ["r1"] };
    renderHistograms(
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

    const embedItem = vi.fn().mockResolvedValue(undefined);
    window.Bokeh = { embed: { embed_item: embedItem } };

    const item = { doc: {}, root_ids: ["r1"] };
    renderHistograms(
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

    await waitFor(() => expect(embedItem).toHaveBeenCalledTimes(1));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Jobs by queue: enlarge chart",
      }),
    );
    await waitFor(() => expect(embedItem).toHaveBeenCalledTimes(2));
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
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });
});
