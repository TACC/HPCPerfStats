import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, afterEach, describe, expect, it } from "vitest";
import BokehEmbed from "./BokehEmbed";
import { SessionContext } from "../session-context";

function renderBokehEmbed(ui, session = null) {
  return render(<SessionContext.Provider value={session}>{ui}</SessionContext.Provider>);
}

describe("BokehEmbed", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.Bokeh;
  });

  it("keeps json_item target in layout (not display:none) while embedding", async () => {
    const embedItem = vi.fn();
    window.Bokeh = { embed: { embed_item: embedItem } };

    const item = { doc: {}, root_ids: ["r1"] };

    const { container } = renderBokehEmbed(
      <BokehEmbed item={item} id="bokeh-test-slot" plotName="Test plot" />
    );

    const slot = container.querySelector("#bokeh-test-slot");
    expect(slot).toBeTruthy();
    expect(slot.style.display).not.toBe("none");

    await waitFor(() => {
      expect(embedItem).toHaveBeenCalledWith(item, "bokeh-test-slot");
    });
  });

  it("shows default unavailable text and reveals/copies API error for staff", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    renderBokehEmbed(
      <BokehEmbed
        plotName="Heatmap"
        unavailableReason="Missing CPI counters in host_data"
      />,
      { is_staff: true }
    );

    expect(screen.getByText("Unavailable — Data not available.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show plot error details" }));

    expect(screen.getByRole("region", { name: "Plot error details" })).toHaveTextContent(
      "Missing CPI counters in host_data",
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy error detail" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("Missing CPI counters in host_data");
    });
    expect(screen.getByText("Copied")).toBeInTheDocument();
  });

  it("hides error detail UI for non-staff users", () => {
    renderBokehEmbed(
      <BokehEmbed
        plotName="Heatmap"
        unavailableReason="Missing CPI counters in host_data"
      />,
      { is_staff: false }
    );

    expect(screen.getByText("Unavailable — Data not available.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show plot error details" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy error detail" })).not.toBeInTheDocument();
  });

  it("shows loading message while external plot query is still running", () => {
    renderBokehEmbed(
      <BokehEmbed
        plotName="Summary plot"
        isLoadingExternal
      />
    );

    expect(screen.getByText("Loading Summary plot…")).toBeInTheDocument();
    expect(screen.queryByText("Unavailable — Data not available.")).not.toBeInTheDocument();
  });

  it("stops Bokeh readiness polling when unmounted before Bokeh loads", async () => {
    vi.useFakeTimers();
    const clearIntervalSpy = vi.spyOn(global, "clearInterval");
    delete window.Bokeh;

    const { unmount } = renderBokehEmbed(
      <BokehEmbed item={{ doc: {}, root_ids: ["r1"] }} id="bokeh-timer-test" />
    );

    unmount();
    await vi.runOnlyPendingTimersAsync();
    expect(clearIntervalSpy).toHaveBeenCalled();
    clearIntervalSpy.mockRestore();
    vi.useRealTimers();
  });

  it("does not execute legacy script/div payloads", () => {
    const embedItem = vi.fn();
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderBokehEmbed(
      <BokehEmbed
        script={'<script type="text/javascript">window.__injected = true;</script>'}
        div={'<div id="unsafe"></div>'}
      />
    );

    expect(screen.getByText("Unavailable — Data not available.")).toBeInTheDocument();
    expect(embedItem).not.toHaveBeenCalled();
    expect(window.__injected).toBeUndefined();
  });

  it("subscribes to window resize after a width-maximized embed becomes ready", async () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const embedItem = vi.fn(() => Promise.resolve());
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderBokehEmbed(
      <BokehEmbed
        item={{ doc: {}, root_ids: ["r1"] }}
        id="bokeh-resize-test"
        plotName="Test"
        maximizeInContainer="width"
      />
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalled());
    await waitFor(() => {
      expect(addSpy.mock.calls.some((call) => call[0] === "resize")).toBe(true);
    });
    addSpy.mockRestore();
  });
});
