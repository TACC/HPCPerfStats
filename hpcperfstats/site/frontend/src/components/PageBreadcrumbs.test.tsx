import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PageBreadcrumbs from "./PageBreadcrumbs";

function renderBreadcrumbs(items) {
  return render(
    <>
      <PageBreadcrumbs items={items} />
    </>,
  );
}

describe("PageBreadcrumbs", () => {
  it("renders nothing when items are empty or missing", () => {
    const { container: empty } = renderBreadcrumbs([]);
    expect(empty).toBeEmptyDOMElement();
    const { container: missing } = renderBreadcrumbs(undefined);
    expect(missing).toBeEmptyDOMElement();
  });

  it("renders linked ancestors and marks the terminal crumb as current page", () => {
    renderBreadcrumbs([
      { label: "Browse", to: "/" },
      { label: "Jobs", to: "/jobs" },
      { label: "Job 42" },
    ]);

    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute("href", "/jobs");
    const current = screen.getByText("Job 42");
    expect(current.closest("li")).toHaveAttribute("aria-current", "page");
    expect(current.closest("li")).toHaveClass("active");
  });

  it("renders plain text when the last item has no link", () => {
    renderBreadcrumbs([{ label: "Browse", to: "/" }, { label: "Year 2024" }]);
    expect(screen.queryByRole("link", { name: "Year 2024" })).not.toBeInTheDocument();
    expect(screen.getByText("Year 2024")).toBeInTheDocument();
  });
});
