import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BannerErrorMessage from "./BannerErrorMessage";

describe("BannerErrorMessage", () => {
  it("renders message with default container styling", () => {
    render(<BannerErrorMessage message="bad" />);
    const el = screen.getByRole("alert");
    expect(el).toHaveTextContent("Error: bad");
    expect(el.className).toContain("container");
    expect(el.className).toContain("text-danger");
  });

  it("inline variant renders full message without Error prefix", () => {
    render(
      <BannerErrorMessage
        variant="inline"
        message="Error loading cache stats: boom"
      />,
    );
    const el = screen.getByRole("alert");
    expect(el).toHaveTextContent("Error loading cache stats: boom");
    expect(el.textContent).not.toMatch(/^Error: Error loading/);
  });
});
