import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import JobListFilterSummary from "./JobListFilterSummary";
import { ExtendedSearchLayoutContext } from "../context/extended-search-layout-context";

function renderSummary(lines, openExtendedSearch = vi.fn()) {
  return render(
    <ExtendedSearchLayoutContext.Provider value={{ openExtendedSearch }}>
      <JobListFilterSummary lines={lines} />
    </ExtendedSearchLayoutContext.Provider>,
  );
}

describe("JobListFilterSummary", () => {
  it("renders nothing when there are no filter lines", () => {
    const { container } = renderSummary([]);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists active filters and opens extended search when Modify search is clicked", async () => {
    const user = userEvent.setup();
    const openExtendedSearch = vi.fn();
    renderSummary(
      ["Runtime minimum: 3600 seconds", "Queue: normal"],
      openExtendedSearch,
    );

    const region = screen.getByRole("region", { name: "Active search filters" });
    expect(region).toBeInTheDocument();
    expect(screen.getByText("Active filters")).toBeInTheDocument();
    expect(screen.getByText("Runtime minimum: 3600 seconds")).toBeInTheDocument();
    expect(screen.getByText("Queue: normal")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /modify search/i }));
    expect(openExtendedSearch).toHaveBeenCalledTimes(1);
  });
});
