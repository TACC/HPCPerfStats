import { afterEach, describe, expect, it, vi } from "vitest";
import {
  captureBokehTargetDataUrl,
  captureJobDetailPrintBokehSnapshots,
  disposeBokehViewsForTarget,
  disposeJobDetailPrintBokehTargets,
  jobDetailPrintBokehTargetIds,
  PRINT_SNAPSHOT_FALLBACK_PNG,
} from "./job-detail-print-snapshot";

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
});

describe("disposeBokehViewsForTarget", () => {
  it("no-ops without Bokeh index", () => {
    expect(() => disposeBokehViewsForTarget(document.createElement("div"))).not.toThrow();
  });
});
