import { render, screen } from "@testing-library/react";
import TabStatusMessage from "./TabStatusMessage";

describe("TabStatusMessage", () => {
  it("renders children with centered padding classes", () => {
    render(<TabStatusMessage>Plots not yet completed.</TabStatusMessage>);
    const el = screen.getByText("Plots not yet completed.");
    expect(el.tagName).toBe("P");
    expect(el).toHaveClass("tab-status-message");
    expect(el).toHaveClass("text-center");
    expect(el).toHaveClass("pt-8");
    expect(el).toHaveClass("pb-16");
    expect(el).not.toHaveAttribute("role");
  });

  it("sets role and aria-live when role is status", () => {
    render(
      <TabStatusMessage role="status">Loading job-level metrics…</TabStatusMessage>,
    );
    const el = screen.getByText("Loading job-level metrics…");
    expect(el).toHaveAttribute("role", "status");
    expect(el).toHaveAttribute("aria-live", "polite");
  });

  it("passes role=note without aria-live", () => {
    render(<TabStatusMessage role="note">Note text</TabStatusMessage>);
    const el = screen.getByRole("note");
    expect(el).toHaveTextContent("Note text");
    expect(el).not.toHaveAttribute("aria-live");
  });
});
