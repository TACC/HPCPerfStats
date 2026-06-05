import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SortableTableHeader from "./SortableTableHeader";

describe("SortableTableHeader", () => {
  it("renders a sortable column header with ascending aria-sort when active", () => {
    const onSort = vi.fn();
    render(
      <table>
        <thead>
          <tr>
            <SortableTableHeader
              column="username"
              sortKey="username"
              sortDir="asc"
              onSort={onSort}
            >
              User
            </SortableTableHeader>
          </tr>
        </thead>
      </table>,
    );

    const header = screen.getByRole("columnheader", { name: /user/i });
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByRole("button", { name: /user/i })).toHaveTextContent("User▲");
  });

  it("calls onSort with the column id when the header button is clicked", async () => {
    const user = userEvent.setup();
    const onSort = vi.fn();
    render(
      <table>
        <thead>
          <tr>
            <SortableTableHeader
              column="failed_rate"
              sortKey="username"
              sortDir="desc"
              onSort={onSort}
            >
              % failed
            </SortableTableHeader>
          </tr>
        </thead>
      </table>,
    );

    await user.click(screen.getByRole("button", { name: /% failed/i }));
    expect(onSort).toHaveBeenCalledWith("failed_rate");
    expect(screen.getByRole("columnheader")).not.toHaveAttribute("aria-sort");
  });
});
