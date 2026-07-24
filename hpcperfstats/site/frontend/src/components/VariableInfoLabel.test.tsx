import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VariableInfoLabel } from "./VariableInfoLabel";

describe("VariableInfoLabel", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

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
    expect(screen.getByText("avg_cpuusage").closest(".inline-flex")).toBeTruthy();
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

  it("renders superscript help trigger close to label text", () => {
    render(
      <VariableInfoLabel variableName="avg_cpuusage" labelText="avg_cpuusage" enableHelp />,
    );
    const help = screen.getByTestId("variable-info-help");
    expect(help).toHaveClass("align-super");
    expect(help).not.toHaveClass("min-h-11");
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
    const label = screen.getByText("Average effective CPU frequency").closest(".inline-flex");
    expect(label).toBeTruthy();
    const children = Array.from(label!.children).map((el) => el.className);
    expect(children[0]).toBe("min-w-0");
    expect(children[1]).toBe("job-detail-metric-units");
    expect(children[2]).toBe("inline-flex shrink-0 items-baseline");
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

  it("shows help popover above sticky analysis tabs", async () => {
    render(
      <div className="job-detail-analysis-tabs">
        <VariableInfoLabel variableName="utilization" labelText="utilization" enableHelp />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: /help: utilization/i }));
    const panel = await screen.findByTestId("variable-info-tooltip");
    expect(panel).toHaveAttribute("data-open");
    expect(document.body.contains(panel)).toBe(true);
    expect(panel).toHaveTextContent(/GPU utilization/i);
  });

  it("configures Base UI hover open on help trigger when not pinned", () => {
    render(
      <VariableInfoLabel variableName="utilization" labelText="utilization" enableHelp />,
    );
    const help = screen.getByRole("button", { name: /help: utilization/i });
    expect(help).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(help);
    expect(help).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByTestId("variable-info-tooltip");
    expect(panel).toHaveAttribute("data-open");
    fireEvent.click(help);
    expect(help).toHaveAttribute("aria-expanded", "false");
  });

  it("closes pinned help panel with the X button", async () => {
    render(
      <VariableInfoLabel variableName="utilization" labelText="utilization" enableHelp />,
    );
    fireEvent.click(screen.getByRole("button", { name: /help: utilization/i }));
    const panel = await screen.findByTestId("variable-info-tooltip");
    expect(panel).toHaveAttribute("data-open");
    fireEvent.click(screen.getByTestId("variable-info-close"));
    expect(screen.getByRole("button", { name: /help: utilization/i })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("closes pinned help panel on Escape", async () => {
    render(
      <VariableInfoLabel variableName="utilization" labelText="utilization" enableHelp />,
    );
    fireEvent.click(screen.getByRole("button", { name: /help: utilization/i }));
    const panel = await screen.findByTestId("variable-info-tooltip");
    expect(panel).toHaveAttribute("data-open");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("button", { name: /help: utilization/i })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });
});
