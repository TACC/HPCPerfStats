import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { buildHostPlotParamsFromSearch } from "../views/HostDetail";

const SRC_ROOT = join(import.meta.dirname, "..");

/** Allowed pointer-events-none in views (decorative pagination spans, documented). */
const POINTER_EVENTS_ALLOWLIST = new Set([
  join(SRC_ROOT, "views/JobList.tsx"),
]);

function collectViewFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (entry === "__tests__") continue;
      collectViewFiles(full, out);
      continue;
    }
    if (entry.endsWith(".tsx") && !entry.endsWith(".test.tsx")) {
      out.push(full);
    }
  }
  return out;
}

describe("interactive-ready drift guard", () => {
  it("forbids pointer-events-none on view surfaces outside allowlist", () => {
    const offenders: string[] = [];
    for (const file of collectViewFiles(join(SRC_ROOT, "views"))) {
      if (POINTER_EVENTS_ALLOWLIST.has(file)) continue;
      const text = readFileSync(file, "utf8");
      if (text.includes("pointer-events-none")) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("requires JobList progressive tableBusy contract test", () => {
    const jobListTest = readFileSync(
      join(SRC_ROOT, "views/__tests__/JobList.test.tsx"),
      "utf8",
    );
    expect(jobListTest).toMatch(/tableBusy/);
    expect(jobListTest).toMatch(/pointer-events-none/);
  });

  it("requires useFocusTrap test for focus-outside panel", () => {
    const trapTest = readFileSync(
      join(SRC_ROOT, "hooks/useFocusTrap.test.ts"),
      "utf8",
    );
    expect(trapTest).toMatch(/outside|does not preventDefault Tab when focus is outside/i);
  });

  it("JobList histograms use filter-identity params (no order_by/page)", () => {
    const jobList = readFileSync(join(SRC_ROOT, "views/JobList.tsx"), "utf8");
    expect(jobList).toMatch(/buildJobListHistogramApiParams/);
    expect(jobList).toMatch(/useJobListHistograms\(\s*histogramApiParams/);
  });

  it("HostDetail and TypeDetail use detailBusy (not bare loading blank on refetch)", () => {
    const host = readFileSync(join(SRC_ROOT, "views/HostDetail.tsx"), "utf8");
    const type = readFileSync(join(SRC_ROOT, "views/TypeDetail.tsx"), "utf8");
    expect(host).toMatch(/detailBusy/);
    expect(type).toMatch(/detailBusy/);
    const hostHook = readFileSync(join(SRC_ROOT, "hooks/use-host-plot.ts"), "utf8");
    const typeHook = readFileSync(join(SRC_ROOT, "hooks/use-type-detail.ts"), "utf8");
    expect(hostHook).toMatch(/keepPreviousData/);
    expect(typeHook).toMatch(/keepPreviousData/);
  });

  it("use-job-plots clears on pk change via prevPkRef (not enabled alone)", () => {
    const plots = readFileSync(join(SRC_ROOT, "hooks/use-job-plots.ts"), "utf8");
    expect(plots).toMatch(/prevPkRef/);
    expect(plots).toMatch(/pkChanged/);
  });

  it("AdminMonitor section hook uses keepPreviousData", () => {
    const hook = readFileSync(
      join(SRC_ROOT, "hooks/use-admin-monitor-section.ts"),
      "utf8",
    );
    expect(hook).toMatch(/keepPreviousData/);
  });

  it("HistogramThumbnails and FilterMultiCombobox reset on filter identity", () => {
    const thumbs = readFileSync(
      join(SRC_ROOT, "components/HistogramThumbnails.tsx"),
      "utf8",
    );
    const combo = readFileSync(
      join(SRC_ROOT, "components/FilterMultiCombobox.tsx"),
      "utf8",
    );
    expect(thumbs).toMatch(/filterIdentitySearchParamsKey/);
    expect(combo).toMatch(/filterIdentitySearchParamsKey/);
  });

  it("forbids nested max-h list virtualizers on JobList / JobMonitor / AdminMonitor", () => {
    for (const name of ["JobList.tsx", "JobMonitor.tsx", "AdminMonitor.tsx"] as const) {
      const text = readFileSync(join(SRC_ROOT, "views", name), "utf8");
      expect(text).not.toMatch(/useVirtualizer/);
      expect(text).not.toMatch(/max-h-\[(?:70|60)vh\]\s+overflow-auto/);
    }
  });

  it("BokehEmbed yields to main thread before prepare/embed", () => {
    const embed = readFileSync(join(SRC_ROOT, "components/BokehEmbed.tsx"), "utf8");
    expect(embed).toMatch(/yieldToMainThread/);
    expect(embed).toMatch(/prepareBokehJsonItemForEmbed/);
  });

  it("Job Detail rank-0 print freezes plots, uses previewMode, and React-owned snapshots", () => {
    const jobDetail = readFileSync(join(SRC_ROOT, "views/JobDetail.tsx"), "utf8");
    expect(jobDetail).toMatch(/previewMode=\{printMountsPlotPanels/);
    expect(jobDetail).toMatch(/PRINT_EMBED_STAGGER/);
    expect(jobDetail).toMatch(/mergePrintPlotsFreeze/);
    expect(jobDetail).toMatch(/mergePrintMultiprecisionFreeze/);
    expect(jobDetail).toMatch(/captureJobDetailPrintBokehSnapshots/);
    expect(jobDetail).toMatch(/printPlotSnapshots/);
    expect(jobDetail).toMatch(/job-detail-print-plot-snapshot/);
    const globalsCss = readFileSync(join(SRC_ROOT, "globals.css"), "utf8");
    expect(globalsCss).toMatch(
      /\[data-job-detail-print="1"\] \[data-testid="variable-info-help"\]/,
    );
    expect(globalsCss).toMatch(/bokeh-plot-unavailable/);
    expect(globalsCss).toMatch(/display:\s*none\s*!important/);
    const designRule = readFileSync(
      join(SRC_ROOT, "../../../cursor-rules/design-focused-spa-ux.mdc"),
      "utf8",
    );
    expect(designRule).toMatch(/previewMode/);
    expect(designRule).toMatch(/React state/);
    expect(designRule).toMatch(/VariableInfoLabel/);
  });

  it("JobList histogram thumbs use previewMode; Enlarge path does not", () => {
    const thumbs = readFileSync(
      join(SRC_ROOT, "components/HistogramThumbnails.tsx"),
      "utf8",
    );
    expect(thumbs).toMatch(/previewMode/);
    // Desktop thumb + mobile grid set previewMode; enlarge dialog block must not.
    const enlargeBlock = thumbs.slice(thumbs.indexOf("histogram-thumbnail-popover-plot"));
    expect(enlargeBlock).not.toMatch(/previewMode/);
    // Fixed-size backend thumbs must not stretch_width (zoomed-in flicker); enlarge may.
    const thumbShellBlock = thumbs.slice(
      0,
      thumbs.indexOf("histogram-thumbnail-popover-plot"),
    );
    expect(thumbShellBlock).not.toMatch(/maximizeInContainer/);
    expect(enlargeBlock).toMatch(/maximizeInContainer/);
    const rule = readFileSync(
      join(
        SRC_ROOT,
        "../../../cursor-rules/interactive-ready-controls.mdc",
      ),
      "utf8",
    );
    expect(rule).toMatch(/previewMode/);
  });
});

describe("buildHostPlotParamsFromSearch", () => {
  it("ignores unrelated query keys for plot identity", () => {
    const a = buildHostPlotParamsFromSearch(
      "n1",
      "end_time__gte=2024-01-01T00:00:00&end_time__lte=now()&order_by=-end_time",
    );
    const b = buildHostPlotParamsFromSearch(
      "n1",
      "end_time__gte=2024-01-01T00:00:00&end_time__lte=now()&foo=bar",
    );
    expect(a).toEqual(b);
  });
});
