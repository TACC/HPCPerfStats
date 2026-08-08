import { afterEach, describe, expect, it, vi } from "vitest";
import {
  captureBokehTargetDataUrl,
  captureBokehTargetDataUrlViaExport,
  captureJobDetailPrintBokehSnapshots,
  collectBokehCanvases,
  disposeBokehViewsForTarget,
  disposeJobDetailPrintBokehTargets,
  findBokehExportRootForTarget,
  jobDetailPrintBokehTargetIds,
  targetHasPrintableBokehCanvases,
  waitForPrintBokehCanvases,
  PRINT_SNAPSHOT_FALLBACK_PNG,
} from "./job-detail-print-snapshot";

/** Nest a canvas like Bokeh 3.x: figure → shadow → canvas host → shadow → canvas.bk-layer. */
function appendBokehShadowCanvas(
  target: HTMLElement,
  opts: { width?: number; height?: number } = {},
): HTMLCanvasElement {
  const width = opts.width ?? 40;
  const height = opts.height ?? 20;
  const figure = document.createElement("div");
  figure.className = "bk-Figure";
  const figureShadow = figure.attachShadow({ mode: "open" });
  const canvasHost = document.createElement("div");
  canvasHost.className = "bk-Canvas";
  const canvasShadow = canvasHost.attachShadow({ mode: "open" });
  const canvas = document.createElement("canvas");
  canvas.className = "bk-layer";
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  expect(ctx).toBeTruthy();
  ctx!.fillStyle = "#123456";
  ctx!.fillRect(0, 0, width, height);
  canvasShadow.appendChild(canvas);
  figureShadow.appendChild(canvasHost);
  target.appendChild(figure);
  return canvas;
}

describe("jobDetailPrintBokehTargetIds", () => {
  it("builds Job Detail embed ids for pk", () => {
    expect(jobDetailPrintBokehTargetIds("99")).toEqual([
      "job-mscript-99",
      "job-roofline-99",
      "job-gpu-roofline-99",
      "job-multiprecision-cpu-99",
      "job-multiprecision-gpu-99",
    ]);
  });
});

describe("collectBokehCanvases", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("finds canvases nested in open shadow roots", () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const canvas = appendBokehShadowCanvas(target);
    expect(target.querySelectorAll("canvas").length).toBe(0);
    expect(collectBokehCanvases(target)).toEqual([canvas]);
  });
});

describe("captureBokehTargetDataUrl", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    // @ts-expect-error test cleanup
    delete window.Bokeh;
  });

  it("returns a data URL without mutating the embed target", () => {
    const target = document.createElement("div");
    target.id = "job-mscript-1";
    const bk = document.createElement("div");
    bk.className = "bk-root";
    const canvas = document.createElement("canvas");
    canvas.width = 40;
    canvas.height = 20;
    const ctx = canvas.getContext("2d");
    expect(ctx).toBeTruthy();
    ctx!.fillStyle = "#123456";
    ctx!.fillRect(0, 0, 40, 20);
    bk.appendChild(canvas);
    target.appendChild(bk);
    document.body.appendChild(target);

    const dataUrl = captureBokehTargetDataUrl(target);
    expect(dataUrl).toMatch(/^data:image\/png/);
    expect(target.querySelector(".bk-root")).toBeTruthy();
    expect(target.querySelector("canvas")).toBeTruthy();
    expect(target.querySelector("img")).toBeNull();
  });

  it("captures canvases nested in Bokeh-style shadow roots", () => {
    const target = document.createElement("div");
    target.id = "job-mscript-1";
    document.body.appendChild(target);
    appendBokehShadowCanvas(target);

    expect(target.querySelectorAll("canvas").length).toBe(0);
    const dataUrl = captureBokehTargetDataUrl(target);
    expect(dataUrl).toMatch(/^data:image\/png/);
    expect(target.querySelector("img")).toBeNull();
    expect(target.querySelector(".bk-Figure")).toBeTruthy();
  });

  it("returns null when there are no canvases", () => {
    const target = document.createElement("div");
    target.appendChild(document.createElement("div")).className = "bk-root";
    expect(captureBokehTargetDataUrl(target)).toBeNull();
  });

  it("prefers Bokeh view.export(png) over shadow canvas composite", () => {
    const target = document.createElement("div");
    target.id = "job-multiprecision-gpu-9";
    const bk = document.createElement("div");
    bk.className = "bk-root";
    target.appendChild(bk);
    document.body.appendChild(target);
    // Shadow canvas would be used only if export path fails.
    appendBokehShadowCanvas(target, { width: 8, height: 8 });

    const exportCanvas = document.createElement("canvas");
    exportCanvas.width = 16;
    exportCanvas.height = 16;
    const exportDataUrl =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    exportCanvas.toDataURL = vi.fn(() => exportDataUrl);
    const exportFn = vi.fn(() => ({ canvas: exportCanvas }));
    window.Bokeh = {
      index: { root: { el: bk, export: exportFn } },
    } as unknown as typeof window.Bokeh;

    expect(findBokehExportRootForTarget(target)?.el).toBe(bk);
    const viaExport = captureBokehTargetDataUrlViaExport(target);
    expect(exportFn).toHaveBeenCalledWith("png");
    expect(viaExport).toBe(exportDataUrl);

    exportFn.mockClear();
    const dataUrl = captureBokehTargetDataUrl(target);
    expect(exportFn).toHaveBeenCalledWith("png");
    expect(dataUrl).toBe(exportDataUrl);
  });

  it("falls back to canvas composite when export is missing", () => {
    const target = document.createElement("div");
    target.id = "job-mscript-8";
    document.body.appendChild(target);
    appendBokehShadowCanvas(target);
    window.Bokeh = { index: {} } as unknown as typeof window.Bokeh;

    expect(captureBokehTargetDataUrlViaExport(target)).toBeNull();
    expect(captureBokehTargetDataUrl(target)).toMatch(/^data:image\/png/);
  });
});

describe("captureJobDetailPrintBokehSnapshots", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("maps existing targets to data URLs without injecting imgs", () => {
    const target = document.createElement("div");
    target.id = "job-roofline-42";
    const canvas = document.createElement("canvas");
    canvas.width = 10;
    canvas.height = 10;
    target.appendChild(canvas);
    document.body.appendChild(target);

    const snaps = captureJobDetailPrintBokehSnapshots("42");
    expect(snaps["job-roofline-42"]).toMatch(/^data:image\/png/);
    expect(snaps["job-roofline-42"]).toBe(PRINT_SNAPSHOT_FALLBACK_PNG);
    expect(target.querySelector("img")).toBeNull();
  });
});

describe("disposeJobDetailPrintBokehTargets", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    // @ts-expect-error test cleanup
    delete window.Bokeh;
  });

  it("disposes Bokeh views and clears the target", () => {
    const target = document.createElement("div");
    target.id = "job-mscript-7";
    const bk = document.createElement("div");
    bk.className = "bk-root";
    target.appendChild(bk);
    document.body.appendChild(target);
    const remove = vi.fn();
    window.Bokeh = {
      index: { v1: { el: bk, remove } },
    } as unknown as typeof window.Bokeh;

    disposeJobDetailPrintBokehTargets("7");
    expect(remove).toHaveBeenCalled();
    expect(target.innerHTML).toBe("");
  });

  it("disposes only captured ids and leaves uncaptured targets intact", () => {
    const captured = document.createElement("div");
    captured.id = "job-roofline-1";
    captured.appendChild(document.createElement("div")).className = "bk-root";
    const kept = document.createElement("div");
    kept.id = "job-mscript-1";
    kept.appendChild(document.createElement("div")).className = "bk-keep";
    document.body.appendChild(captured);
    document.body.appendChild(kept);

    disposeJobDetailPrintBokehTargets("1", ["job-roofline-1"]);
    expect(captured.innerHTML).toBe("");
    expect(kept.querySelector(".bk-keep")).toBeTruthy();
  });
});

describe("disposeBokehViewsForTarget", () => {
  it("no-ops without Bokeh index", () => {
    expect(() => disposeBokehViewsForTarget(document.createElement("div"))).not.toThrow();
  });
});

describe("waitForPrintBokehCanvases", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("resolves immediately when mounted targets already have printable canvases", async () => {
    const target = document.createElement("div");
    target.id = "job-mscript-3";
    document.body.appendChild(target);
    appendBokehShadowCanvas(target, { width: 20, height: 10 });
    expect(targetHasPrintableBokehCanvases(target)).toBe(true);

    await expect(
      waitForPrintBokehCanvases("3", { timeoutMs: 200, pollMs: 20 }),
    ).resolves.toBeUndefined();
  });

  it("times out without hanging when a mounted target never gets canvases", async () => {
    const target = document.createElement("div");
    target.id = "job-mscript-4";
    target.appendChild(document.createElement("div")).className = "bk-root";
    document.body.appendChild(target);
    expect(targetHasPrintableBokehCanvases(target)).toBe(false);

    const started = Date.now();
    await waitForPrintBokehCanvases("4", { timeoutMs: 80, pollMs: 20 });
    expect(Date.now() - started).toBeGreaterThanOrEqual(70);
  });

  it("resolves when canvases appear before timeout", async () => {
    const target = document.createElement("div");
    target.id = "job-roofline-5";
    target.appendChild(document.createElement("div")).className = "bk-root";
    document.body.appendChild(target);

    window.setTimeout(() => {
      target.innerHTML = "";
      appendBokehShadowCanvas(target, { width: 12, height: 8 });
    }, 30);

    await waitForPrintBokehCanvases("5", { timeoutMs: 500, pollMs: 15 });
    expect(targetHasPrintableBokehCanvases(target)).toBe(true);
  });
});
