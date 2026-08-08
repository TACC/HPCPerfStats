import { afterEach, describe, expect, it, vi } from "vitest";
import {
  captureBokehTargetDataUrl,
  captureJobDetailPrintBokehSnapshots,
  collectBokehCanvases,
  disposeBokehViewsForTarget,
  disposeJobDetailPrintBokehTargets,
  jobDetailPrintBokehTargetIds,
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
