import { describe, expect, it, vi } from "vitest";
import {
  getBokehDocumentFromEmbedViews,
  waitForBokehEmbedDocumentIdle,
} from "./bokeh-when-document-idle";

function viewManagerWithDoc(doc) {
  return {
    get roots() {
      return [{ model: { document: doc } }];
    },
  };
}

describe("getBokehDocumentFromEmbedViews", () => {
  it("returns null for null/undefined/non-object", () => {
    expect(getBokehDocumentFromEmbedViews(null)).toBeNull();
    expect(getBokehDocumentFromEmbedViews(undefined)).toBeNull();
    expect(getBokehDocumentFromEmbedViews(1)).toBeNull();
  });

  it("returns document from first root view model", () => {
    const doc = { is_idle: true };
    expect(getBokehDocumentFromEmbedViews(viewManagerWithDoc(doc))).toBe(doc);
  });

  it("returns null when roots missing or empty", () => {
    expect(getBokehDocumentFromEmbedViews({ roots: [] })).toBeNull();
    expect(getBokehDocumentFromEmbedViews({})).toBeNull();
  });

  it("uses Symbol.iterator when roots is absent (ViewManager-like)", () => {
    const doc = { is_idle: true };
    const vm = {
      *[Symbol.iterator]() {
        yield { model: { document: doc } };
      },
    };
    expect(getBokehDocumentFromEmbedViews(vm)).toBe(doc);
  });
});

describe("waitForBokehEmbedDocumentIdle", () => {
  it("resolves immediately when document.is_idle is already true", async () => {
    const doc = { is_idle: true, idle: { connect: vi.fn(), disconnect: vi.fn() } };
    await waitForBokehEmbedDocumentIdle(viewManagerWithDoc(doc));
    expect(doc.idle.connect).not.toHaveBeenCalled();
  });

  it("resolves when document.idle emits", async () => {
    const doc = {
      is_idle: false,
      idle: {
        connect(fn) {
          this._fn = fn;
          return true;
        },
        disconnect(fn) {
          if (this._fn === fn) this._fn = null;
        },
        emit() {
          this._fn?.();
        },
        _fn: null,
      },
    };
    const p = waitForBokehEmbedDocumentIdle(viewManagerWithDoc(doc), { timeoutMs: 2000 });
    doc.is_idle = true;
    doc.idle.emit();
    await p;
  });

  it("resolves after connect if is_idle becomes true synchronously", async () => {
    let idle = false;
    const doc = {
      get is_idle() {
        return idle;
      },
      idle: {
        connect() {
          idle = true;
          return true;
        },
        disconnect: vi.fn(),
      },
    };
    await waitForBokehEmbedDocumentIdle(viewManagerWithDoc(doc), { timeoutMs: 2000 });
    expect(idle).toBe(true);
  });

  it("resolves on timeout if idle never fires", async () => {
    vi.useFakeTimers();
    const doc = {
      is_idle: false,
      idle: {
        connect: vi.fn(() => true),
        disconnect: vi.fn(),
      },
    };
    const p = waitForBokehEmbedDocumentIdle(viewManagerWithDoc(doc), { timeoutMs: 100 });
    await vi.advanceTimersByTimeAsync(100);
    await p;
    vi.useRealTimers();
  });

  it("waits fallback delay when embed result has no document handle", async () => {
    vi.useFakeTimers();
    const p = waitForBokehEmbedDocumentIdle(undefined, { fallbackDelayMs: 40 });
    await vi.advanceTimersByTimeAsync(40);
    await p;
    vi.useRealTimers();
  });

  it("waits when document has no idle signal", async () => {
    vi.useFakeTimers();
    const doc = { is_idle: false };
    const p = waitForBokehEmbedDocumentIdle(viewManagerWithDoc(doc), {
      fallbackWhenNoIdleSignalMs: 35,
    });
    await vi.advanceTimersByTimeAsync(35);
    await p;
    vi.useRealTimers();
  });
});
