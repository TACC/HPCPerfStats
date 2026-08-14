import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { vi, afterEach, describe, expect, it } from "vitest";
import BokehEmbed from "./BokehEmbed";
import { SessionContext } from "../session-context";
import { VALID_BOKEH_JSON_ITEM } from "@test/vitest/test-utils/bokeh-fixtures";
import { BOKEH_EMBED_LOCK_SHARDS } from "@/utils/bokeh-embed-defaults";

vi.mock("../bokehInit", () => ({
  ensureBokehLoaded: vi.fn(() => Promise.resolve(globalThis.window?.Bokeh)),
}));

const yieldToMainThreadMock = vi.fn(() => Promise.resolve());
vi.mock("@/utils/yield-main-thread", () => ({
  yieldToMainThread: () => yieldToMainThreadMock(),
}));

function renderBokehEmbed(ui, session = null) {
  return render(<SessionContext.Provider value={session}>{ui}</SessionContext.Provider>);
}
function embedViewsWithIdleDoc() {
  const doc = {
    is_idle: true,
    idle: { connect: vi.fn(), disconnect: vi.fn() },
  };
  return { roots: [{ model: { document: doc } }] };
}

/** scheduleBokehLayoutReflow uses nested rAF + 72ms; maximize adds another nested-rAF resize. */
async function drainBokehResizeBroadcasts() {
  await new Promise((resolve) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setTimeout(resolve, 100);
      });
    });
  });
}

describe("BokehEmbed", () => {
  afterEach(async () => {
    cleanup();
    yieldToMainThreadMock.mockClear();
    delete window.Bokeh;
    // Unmount cancels embed effects but not already-queued resize broadcasts.
    await drainBokehResizeBroadcasts();
  });

  it("keeps json_item target in layout (not display:none) while embedding", async () => {
    const embedItem = vi.fn();
    window.Bokeh = { embed: { embed_item: embedItem } };

    const item = VALID_BOKEH_JSON_ITEM;

    const { container } = renderBokehEmbed(
      <BokehEmbed item={item} id="bokeh-test-slot" plotName="Test plot" />
    );

    const slot = container.querySelector("#bokeh-test-slot");
    expect(slot).toBeTruthy();
    expect(slot.style.display).not.toBe("none");

    await waitFor(() => {
      expect(embedItem).toHaveBeenCalledTimes(1);
    });
    const [payload, targetId] = embedItem.mock.calls[0];
    expect(targetId).toBe("bokeh-test-slot");
    expect(payload.doc.root_ids).toEqual(item.doc.root_ids);
    expect(payload).not.toBe(item);
  });

  it("yields to main thread before embed_item", async () => {
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderBokehEmbed(
      <BokehEmbed
        item={VALID_BOKEH_JSON_ITEM}
        id="bokeh-yield-test"
        plotName="Yield plot"
      />,
    );

    await waitFor(() => {
      expect(embedItem).toHaveBeenCalledTimes(1);
    });
    expect(yieldToMainThreadMock).toHaveBeenCalled();
    expect(yieldToMainThreadMock.mock.invocationCallOrder[0]).toBeLessThan(
      embedItem.mock.invocationCallOrder[0],
    );
  });

  it("waits for Bokeh document idle after embed_item before signaling plot ready", async () => {
    const doc = {
      _done: false,
      get is_idle() {
        return doc._done;
      },
      idle: {
        connect(fn) {
          queueMicrotask(() => {
            doc._done = true;
            fn();
          });
          return true;
        },
        disconnect: vi.fn(),
      },
    };
    const fakeViews = { roots: [{ model: { document: doc } }] };
    const embedItem = vi.fn(() => Promise.resolve(fakeViews));
    window.Bokeh = { embed: { embed_item: embedItem } };
    const onReady = vi.fn();

    renderBokehEmbed(
      <BokehEmbed
        item={VALID_BOKEH_JSON_ITEM}
        id="bokeh-idle-chain-test"
        plotName="Idle chain"
        onPlotReadyChange={onReady}
      />,
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalled());
    await waitFor(() => expect(onReady).toHaveBeenCalledWith(true));
    expect(doc.idle.disconnect).toHaveBeenCalled();
  });

  it("does not re-embed when onPlotReadyChange triggers parent setState", async () => {
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };
    const readyCalls: boolean[] = [];

    function Parent() {
      const [, setReady] = useState(false);
      return (
        <BokehEmbed
          item={VALID_BOKEH_JSON_ITEM}
          id="bokeh-callback-flicker"
          plotName="Flicker"
          onPlotReadyChange={(ready) => {
            readyCalls.push(ready);
            setReady(ready);
          }}
        />
      );
    }

    renderBokehEmbed(<Parent />);
    await waitFor(() => expect(embedItem).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(readyCalls).toContain(true));
    await drainBokehResizeBroadcasts();
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(embedItem).toHaveBeenCalledTimes(1);
  });

  it("uses embedMinHeightPx for plot slot minHeight while placeholder is shown", async () => {
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };

    const item = VALID_BOKEH_JSON_ITEM;
    const { container, unmount } = renderBokehEmbed(
      <BokehEmbed item={item} id="bokeh-minh-test" plotName="Thumb" embedMinHeightPx={200} />,
    );

    const slot = container.querySelector("#bokeh-minh-test");
    expect(slot).toBeTruthy();
    expect(slot.style.minHeight).toBe("200px");

    await waitFor(() => {
      expect(embedItem).toHaveBeenCalled();
    });
    unmount();
  });

  it("shows generic unavailable copy for non-staff when reason is provided", () => {
    const reason = "Missing CPI counters in host_data";
    renderBokehEmbed(
      <BokehEmbed plotName="Heatmap" unavailableReason={reason} />,
      { is_staff: false },
    );

    expect(screen.getByText("Unavailable — Data not available.")).toBeInTheDocument();
    expect(screen.queryByText(reason)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show plot error details" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy error detail" })).not.toBeInTheDocument();
  });

  it("shows staff plot error detail controls when reason is provided", () => {
    const reason = "Missing CPI counters in host_data";
    renderBokehEmbed(
      <BokehEmbed plotName="Heatmap" unavailableReason={reason} />,
      { logged_in: true, username: "alice", is_staff: true },
    );

    expect(screen.getByText("Unavailable — Data not available.")).toBeInTheDocument();
    expect(screen.queryByText(reason)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show plot error details" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy error detail" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show plot error details" }));
    expect(screen.getByText(reason)).toBeInTheDocument();
  });

  it("shows loading message while external plot query is still running", () => {
    renderBokehEmbed(
      <BokehEmbed
        plotName="Summary plots"
        isLoadingExternal
      />
    );

    expect(screen.getByText("Loading Summary plots…")).toBeInTheDocument();
    expect(screen.queryByText("Unavailable — Data not available.")).not.toBeInTheDocument();
  });

  it("stops Bokeh readiness polling when unmounted before Bokeh loads", async () => {
    vi.useFakeTimers();
    const clearIntervalSpy = vi.spyOn(global, "clearInterval");
    delete window.Bokeh;

    try {
      const { unmount } = renderBokehEmbed(
        <BokehEmbed item={VALID_BOKEH_JSON_ITEM} id="bokeh-timer-test" />,
      );

      await Promise.resolve();
      await vi.runOnlyPendingTimersAsync();
      unmount();
      await vi.runOnlyPendingTimersAsync();
      expect(clearIntervalSpy).toHaveBeenCalled();
    } finally {
      clearIntervalSpy.mockRestore();
      vi.useRealTimers();
    }
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
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderBokehEmbed(
      <BokehEmbed
        item={VALID_BOKEH_JSON_ITEM}
        id="bokeh-resize-test"
        plotName="Test"
        maximizeInContainer="width"
      />
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalled());
    await waitFor(() => {
      expect(addSpy.mock.calls.some((call) => call[0] === "resize")).toBe(true);
    });
    await drainBokehResizeBroadcasts();
    addSpy.mockRestore();
  });

  it("previewMode skips global resize reflow and maximize resize listener; marks plot non-interactive", async () => {
    await drainBokehResizeBroadcasts();
    const addSpy = vi.spyOn(window, "addEventListener");
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };

    const { container } = renderBokehEmbed(
      <BokehEmbed
        item={VALID_BOKEH_JSON_ITEM}
        id="bokeh-preview-test"
        plotName="Preview"
        maximizeInContainer="width"
        previewMode
      />,
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalled());
    await waitFor(() => {
      const slot = container.querySelector("#bokeh-preview-test");
      expect(slot).toHaveClass("pointer-events-none");
      expect(slot).toHaveAttribute("data-bokeh-preview", "true");
    });
    expect(screen.getByRole("region", { name: "Chart preview: Preview" })).toBeInTheDocument();

    // Allow rAF / settle timers from maximize/reflow paths to flush; previewMode must
    // not emit global resize or register a maximize-on-resize listener.
    await drainBokehResizeBroadcasts();
    const resizeDispatches = dispatchSpy.mock.calls.filter(
      (call) => call[0] instanceof Event && call[0].type === "resize",
    );
    expect(resizeDispatches).toHaveLength(0);
    expect(addSpy.mock.calls.some((call) => call[0] === "resize")).toBe(false);

    addSpy.mockRestore();
    dispatchSpy.mockRestore();
  });

  it("printCaptureLayout with previewMode one-shots resize but does not subscribe maximize-on-resize", async () => {
    await drainBokehResizeBroadcasts();
    const addSpy = vi.spyOn(window, "addEventListener");
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    const onReady = vi.fn();
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };

    renderBokehEmbed(
      <BokehEmbed
        item={VALID_BOKEH_JSON_ITEM}
        id="bokeh-print-capture-test"
        plotName="Summary plots"
        maximizeInContainer="width"
        previewMode
        printCaptureLayout
        onPlotReadyChange={onReady}
      />,
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalled());
    await waitFor(() => expect(onReady).toHaveBeenCalledWith(true));
    await drainBokehResizeBroadcasts();

    const resizeDispatches = dispatchSpy.mock.calls.filter(
      (call) => call[0] instanceof Event && call[0].type === "resize",
    );
    expect(resizeDispatches.length).toBeGreaterThan(0);
    expect(addSpy.mock.calls.some((call) => call[0] === "resize")).toBe(false);

    addSpy.mockRestore();
    dispatchSpy.mockRestore();
  });

  it("printCaptureLayout strips in-plot help ? Labels before embed_item", async () => {
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };
    const itemWithHelp = {
      ...VALID_BOKEH_JSON_ITEM,
      doc: {
        ...VALID_BOKEH_JSON_ITEM.doc,
        roots: [
          {
            type: "object",
            name: "Figure",
            id: "p9999",
            attributes: {
              center: [
                {
                  type: "object",
                  name: "Label",
                  id: "p8888",
                  attributes: { text: "?" },
                },
              ],
            },
          },
        ],
      },
      root_id: "p9999",
      root_ids: ["p9999"],
    };

    renderBokehEmbed(
      <BokehEmbed
        item={itemWithHelp}
        id="bokeh-print-strip-help"
        plotName="Multiprecision Mix"
        previewMode
        printCaptureLayout
      />,
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalled());
    const [payload] = embedItem.mock.calls[0];
    const payloadJson = JSON.stringify(payload);
    expect(payloadJson).not.toMatch(/"text":"\?"/);
    expect(payloadJson).not.toMatch(/"text": "\?"/);
  });

  it("defers embed until IntersectionObserver reports intersecting when deferEmbedUntilVisible is true", async () => {
    let intersectionCallback = null;
    class MockIntersectionObserver {
      constructor(callback, _options) {
        intersectionCallback = callback;
      }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);

    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    window.Bokeh = { embed: { embed_item: embedItem } };

    try {
      renderBokehEmbed(
        <BokehEmbed
          deferEmbedUntilVisible
          item={VALID_BOKEH_JSON_ITEM}
          id="bokeh-io-defer-test"
          plotName="Deferred"
        />,
      );

      await Promise.resolve();
      expect(embedItem).not.toHaveBeenCalled();
      expect(intersectionCallback).toBeTypeOf("function");
      intersectionCallback([{ isIntersecting: true }]);
      await waitFor(() => expect(embedItem).toHaveBeenCalledTimes(1));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("limits concurrent embed_item via sharded global lock", async () => {
    let concurrent = 0;
    let maxConcurrent = 0;
    const embedItem = vi.fn(async () => {
      concurrent += 1;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      await new Promise((r) => {
        setTimeout(r, 8);
      });
      concurrent -= 1;
      return embedViewsWithIdleDoc();
    });
    window.Bokeh = { embed: { embed_item: embedItem } };
    const item = VALID_BOKEH_JSON_ITEM;

    renderBokehEmbed(
      <>
        <BokehEmbed item={item} id="slot-a" plotName="A" />
        <BokehEmbed item={item} id="slot-b" plotName="B" />
      </>,
    );

    await waitFor(() => expect(embedItem).toHaveBeenCalledTimes(2));
    expect(maxConcurrent).toBeLessThanOrEqual(BOKEH_EMBED_LOCK_SHARDS);
  });

  it("does not call embed_item after unmount aborts the embed pipeline", async () => {
    const embedItem = vi.fn(() => Promise.resolve(embedViewsWithIdleDoc()));
    delete window.Bokeh;

    const { unmount } = renderBokehEmbed(
      <BokehEmbed item={VALID_BOKEH_JSON_ITEM} id="bokeh-unmount-abort" plotName="Unmount" />,
    );

    unmount();
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(embedItem).not.toHaveBeenCalled();
  });
});
