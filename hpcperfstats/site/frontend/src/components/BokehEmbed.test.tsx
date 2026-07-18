import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

describe("BokehEmbed", () => {
  afterEach(() => {
    yieldToMainThreadMock.mockClear();
    delete window.Bokeh;
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
    addSpy.mockRestore();
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
