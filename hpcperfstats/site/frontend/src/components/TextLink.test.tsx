import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExternalTextLink, TextLink, textLinkClassName } from "./TextLink";

describe("TextLink", () => {
  it("renders with link styling classes", () => {
    render(<TextLink href="/machine/job/1/">Job 1</TextLink>);
    const link = screen.getByRole("link", { name: "Job 1" });
    expect(link).toHaveClass("text-link");
    expect(link).toHaveClass("underline");
    expect(link).toHaveAttribute("href", "/machine/job/1");
  });

  it("exports textLinkClassName for raw anchors", () => {
    expect(textLinkClassName()).toContain("text-link");
    expect(textLinkClassName()).toContain("underline");
  });

  it("ExternalTextLink sets rel and target by default", () => {
    render(
      <ExternalTextLink href="https://example.com/logs">Logs</ExternalTextLink>,
    );
    const link = screen.getByRole("link", { name: "Logs" });
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveClass("underline");
  });
});
