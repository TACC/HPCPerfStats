import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VariableInfoLabel } from "./VariableInfoLabel";

describe("VariableInfoLabel", () => {
  it("renders a help marker when metadata exists", () => {
    render(
      <VariableInfoLabel
        variableName="avg_cpuusage"
        labelText="avg_cpuusage"
        enableHelp
      />
    );
    expect(screen.getByText("avg_cpuusage")).toBeInTheDocument();
    expect(screen.getByTestId("variable-info-help")).toHaveTextContent("?");
    expect(screen.getByText("avg_cpuusage").closest(".variable-info-label")).toBeTruthy();
  });

  it("does not render a help marker unless enabled", () => {
    render(
      <VariableInfoLabel variableName="avg_cpuusage" labelText="avg_cpuusage" />
    );
    expect(screen.getByText("avg_cpuusage")).toBeInTheDocument();
    expect(screen.queryByTestId("variable-info-help")).not.toBeInTheDocument();
  });

  it("renders only the label when no metadata exists", () => {
    render(
      <VariableInfoLabel
        variableName="totally_unknown_x"
        labelText="totally_unknown_x"
        enableHelp
      />
    );
    expect(screen.getByText("totally_unknown_x")).toBeInTheDocument();
    expect(screen.getByTestId("variable-info-help")).toBeInTheDocument();
  });

  it("opens help text on button click", () => {
    render(
      <VariableInfoLabel variableName="utilization" labelText="utilization" enableHelp />
    );
    expect(screen.getByText("utilization")).toBeInTheDocument();
    const help = screen.getByRole("button", { name: /help: utilization/i });
    fireEvent.click(help);
    const panel = screen.getByTestId("variable-info-tooltip");
    expect(panel).toHaveTextContent(/GPU utilization/i);
    expect(panel).toBeInTheDocument();
    expect(document.body.contains(panel)).toBe(true);
    expect(panel.classList.contains("variable-info-tooltip-portal")).toBe(true);
  });

  it("renders suffix before the help control when suffixBeforeHelp is set", () => {
    render(
      <VariableInfoLabel
        variableName="avg_freq"
        labelText="Average effective CPU frequency"
        enableHelp
        suffixBeforeHelp={<span className="job-detail-metric-units">[GHz]</span>}
      />,
    );
    const label = screen.getByText("Average effective CPU frequency").closest(".variable-info-label");
    expect(label).toBeTruthy();
    const children = Array.from(label.children).map((el) => el.className);
    expect(children[0]).toBe("variable-info-label-text");
    expect(children[1]).toBe("job-detail-metric-units");
    expect(children[2]).toBe("variable-info-help-wrap");
  });

  it("renders a separator between definition and researcher guidance when both exist", () => {
    render(
      <VariableInfoLabel variableName="avg_cpuusage" labelText="avg_cpuusage" enableHelp />
    );
    fireEvent.click(screen.getByRole("button", { name: /help: avg_cpuusage/i }));
    const panel = screen.getByTestId("variable-info-tooltip");
    expect(panel.querySelector(".variable-info-tooltip-sep")).toBeTruthy();
    expect(panel).toHaveTextContent(/parallel efficiency|OpenMP|MPI/i);
  });
});
