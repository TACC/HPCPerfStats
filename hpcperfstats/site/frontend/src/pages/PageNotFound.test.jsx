import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import PageNotFound from "./PageNotFound";

describe("PageNotFound", () => {
  it("shows a not-found alert with a link back to browse jobs", () => {
    render(
      <MemoryRouter>
        <PageNotFound />
      </MemoryRouter>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
    expect(
      screen.getByText(/that address is not part of hpcperfstats/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /browse jobs/i })).toHaveAttribute("href", "/");
  });
});
