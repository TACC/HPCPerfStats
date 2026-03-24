import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, afterEach, describe, expect, it } from "vitest";
import BokehEmbed from "./BokehEmbed";

describe("BokehEmbed", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.Bokeh;
  });

  it("keeps json_item target in layout (not display:none) while embedding", async () => {
    const embedItem = vi.fn();
    window.Bokeh = { embed: { embed_item: embedItem } };

    const item = { doc: {}, root_ids: ["r1"] };

    const { container } = render(
      <BokehEmbed item={item} id="bokeh-test-slot" plotName="Test plot" />
    );

    const slot = container.querySelector("#bokeh-test-slot");
    expect(slot).toBeTruthy();
    expect(slot.style.display).not.toBe("none");

    await waitFor(() => {
      expect(embedItem).toHaveBeenCalledWith(item, "bokeh-test-slot");
    });
  });

  it("shows default unavailable text and reveals/copies API error in hover details", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <BokehEmbed
        plotName="Heatmap"
        unavailableReason="Missing CPI counters in host_data"
      />
    );

    expect(screen.getByText("Plot not available")).toBeInTheDocument();
    const detailsTrigger = screen.getByLabelText("Show plot error details");
    fireEvent.mouseEnter(detailsTrigger);

    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "Missing CPI counters in host_data"
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("Missing CPI counters in host_data");
    });
    expect(screen.getByText("Copied")).toBeInTheDocument();
  });
});
