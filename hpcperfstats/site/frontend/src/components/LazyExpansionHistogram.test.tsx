import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LazyExpansionHistogram from "./LazyExpansionHistogram";

vi.mock("./BokehPlotWithLimitation", () => ({
  default: (props: Record<string, unknown>) => (
    <div
      data-testid="bokeh-limitation"
      data-preview-mode={props.previewMode === true ? "true" : "false"}
    />
  ),
}));

vi.mock("@/hooks/use-pub-expansion-period", () => ({
  usePubExpansionPeriod: () => ({
    block: {
      expansion_factor_definition: "weekly mean",
      bokeh_histogram_json_item: { root_id: "x" },
    },
    loadError: null,
    loading: false,
  }),
}));

describe("LazyExpansionHistogram", () => {
  it("passes previewMode to Bokeh embeds (multi-chart dashboard preview)", () => {
    render(
      <LazyExpansionHistogram
        grouping="yearly"
        periodKey="2024"
        histogramCaption="cap"
        initialBlock={{
          bokeh_histogram_json_item: { root_id: "x" },
        }}
      />,
    );
    expect(screen.getByTestId("bokeh-limitation")).toHaveAttribute(
      "data-preview-mode",
      "true",
    );
  });
});
