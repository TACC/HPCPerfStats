import { describe, expect, it } from "vitest";
import {
  plotReadinessRank,
  sortByPlotReadiness,
} from "./job-detail-plot-readiness-order";

describe("plotReadinessRank", () => {
  it("ranks ready ahead of loading and unavailable", () => {
    expect(
      plotReadinessRank({
        isLoading: false,
        item: { root_id: "a" },
        unavailableReason: null,
      }),
    ).toBe(0);
    expect(
      plotReadinessRank({
        isLoading: true,
        item: null,
        unavailableReason: null,
      }),
    ).toBe(1);
    expect(
      plotReadinessRank({
        isLoading: false,
        item: null,
        unavailableReason: "Missing CPU roofline",
      }),
    ).toBe(2);
    expect(
      plotReadinessRank({
        isLoading: false,
        item: null,
        unavailableReason: null,
      }),
    ).toBe(2);
  });
});

describe("sortByPlotReadiness", () => {
  it("puts ready before loading before unavailable and keeps catalog ties", () => {
    const items = [
      { id: "cpu", isLoading: true, item: null, unavailableReason: null },
      {
        id: "gpu",
        isLoading: false,
        item: { root_id: "g" },
        unavailableReason: null,
      },
    ];
    expect(sortByPlotReadiness(items, (p) => p).map((p) => p.id)).toEqual([
      "gpu",
      "cpu",
    ]);
  });

  it("keeps catalog order when ranks match", () => {
    const bothReady = [
      {
        id: "cpu",
        isLoading: false,
        item: { root_id: "c" },
        unavailableReason: null,
      },
      {
        id: "gpu",
        isLoading: false,
        item: { root_id: "g" },
        unavailableReason: null,
      },
    ];
    expect(sortByPlotReadiness(bothReady, (p) => p).map((p) => p.id)).toEqual([
      "cpu",
      "gpu",
    ]);

    const bothUnavailable = [
      {
        id: "cpu",
        isLoading: false,
        item: null,
        unavailableReason: "Missing CPU",
      },
      {
        id: "gpu",
        isLoading: false,
        item: null,
        unavailableReason: "Missing GPU",
      },
    ];
    expect(
      sortByPlotReadiness(bothUnavailable, (p) => p).map((p) => p.id),
    ).toEqual(["cpu", "gpu"]);
  });

  it("puts loading before unavailable when neither is ready", () => {
    const items = [
      {
        id: "cpu",
        isLoading: false,
        item: null,
        unavailableReason: "Missing CPU",
      },
      { id: "gpu", isLoading: true, item: null, unavailableReason: null },
    ];
    expect(sortByPlotReadiness(items, (p) => p).map((p) => p.id)).toEqual([
      "gpu",
      "cpu",
    ]);
  });
});
