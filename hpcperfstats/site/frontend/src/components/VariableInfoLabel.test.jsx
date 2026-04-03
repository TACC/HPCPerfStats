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
});
