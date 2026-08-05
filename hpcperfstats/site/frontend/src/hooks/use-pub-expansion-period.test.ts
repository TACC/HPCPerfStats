import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { usePubExpansionPeriod } from "./use-pub-expansion-period";

vi.mock("@/api/fetch-mutator", () => ({
  fetchPubExpansionPeriod: vi.fn(),
}));

describe("usePubExpansionPeriod", () => {
  it("keeps the same block and bokeh item refs when initialBlock fingerprint is unchanged", () => {
    const item = { root_id: "h1", doc: { bins: [1, 2] } };
    const first = {
      bokeh_histogram_json_item: item,
      histogram_bin_edges: [0, 1],
      histogram_counts: [3],
    };
    const second = {
      ...first,
      bokeh_histogram_json_item: { ...item },
    };

    const { result, rerender } = renderHook(
      ({ block }) => usePubExpansionPeriod("yearly", "2024", true, block),
      { initialProps: { block: first } },
    );

    const blockRef = result.current.block;
    const itemRef = result.current.block?.bokeh_histogram_json_item;

    rerender({ block: second });

    expect(result.current.block).toBe(blockRef);
    expect(result.current.block?.bokeh_histogram_json_item).toBe(itemRef);
  });

  it("replaces block when bokeh_histogram_json_item fingerprint changes", async () => {
    const first = {
      bokeh_histogram_json_item: { root_id: "h1", doc: { bins: [1] } },
    };
    const second = {
      bokeh_histogram_json_item: { root_id: "h1", doc: { bins: [1, 2] } },
    };

    const { result, rerender } = renderHook(
      ({ block }) => usePubExpansionPeriod("yearly", "2024", true, block),
      { initialProps: { block: first } },
    );

    await act(async () => {
      rerender({ block: second });
    });

    expect(result.current.block).toBe(second);
    expect(result.current.block?.bokeh_histogram_json_item).toEqual(
      second.bokeh_histogram_json_item,
    );
  });
});
