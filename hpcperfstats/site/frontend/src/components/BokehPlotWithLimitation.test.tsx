import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BokehPlotWithLimitation from "./BokehPlotWithLimitation";

describe("BokehPlotWithLimitation", () => {
  it("wires aria-describedby to hidden limitation prose", () => {
    render(
      <BokehPlotWithLimitation
        id="plot-test"
        plotName="Test plot"
        item={null}
        unavailableReason="No data"
      />,
    );
    const region = screen.getByRole("region", { name: /interactive chart: test plot/i });
    const describedBy = region.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const description = document.getElementById(describedBy);
    expect(description).toHaveClass("sr-only");
    expect(description?.textContent).toMatch(/assistive technology/i);
  });
});
