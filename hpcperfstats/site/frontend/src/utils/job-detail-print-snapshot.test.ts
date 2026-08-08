import { afterEach, describe, expect, it, vi } from "vitest";
import {
  disposeBokehViewsForTarget,
  jobDetailPrintBokehTargetIds,
  snapshotBokehTargetToStaticImage,
  snapshotJobDetailPrintBokehTargets,
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

describe("snapshotBokehTargetToStaticImage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    // @ts-expect-error test cleanup
    delete window.Bokeh;
  });

  it("replaces canvases with a static img and disposes Bokeh views", () => {
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

    const remove = vi.fn();
    const viewEl = document.createElement("div");
    bk.appendChild(viewEl);
    window.Bokeh = {
      index: {
        v1: { el: viewEl, remove },
      },
    } as unknown as typeof window.Bokeh;

    const ok = snapshotBokehTargetToStaticImage(target);
    expect(ok).toBe(true);
    expect(remove).toHaveBeenCalled();
    expect(target.querySelector(".bk-root")).toBeNull();
    expect(target.querySelectorAll("canvas").length).toBe(0);
    const img = target.querySelector("img.job-detail-print-plot-snapshot");
    expect(img).toBeTruthy();
    expect((img as HTMLImageElement).src).toMatch(/^data:image\/png/);
  });

  it("disposes and clears bk-root when no canvases", () => {
    const target = document.createElement("div");
    const bk = document.createElement("div");
    bk.className = "bk-root";
    target.appendChild(bk);
    document.body.appendChild(target);
    const remove = vi.fn();
    window.Bokeh = {
      index: { v1: { el: bk, remove } },
    } as unknown as typeof window.Bokeh;

    expect(snapshotBokehTargetToStaticImage(target)).toBe(false);
    expect(remove).toHaveBeenCalled();
    expect(target.innerHTML).toBe("");
  });
});

describe("snapshotJobDetailPrintBokehTargets", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("snapshots only existing targets for pk", () => {
    const target = document.createElement("div");
    target.id = "job-roofline-42";
    const canvas = document.createElement("canvas");
    canvas.width = 10;
    canvas.height = 10;
    target.appendChild(canvas);
    document.body.appendChild(target);

    snapshotJobDetailPrintBokehTargets("42");
    expect(target.querySelector("img.job-detail-print-plot-snapshot")).toBeTruthy();
  });
});

describe("disposeBokehViewsForTarget", () => {
  it("no-ops without Bokeh index", () => {
    expect(() => disposeBokehViewsForTarget(document.createElement("div"))).not.toThrow();
  });
});
