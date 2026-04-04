import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Search from "./Search";

vi.mock("../hooks/use-home-options", () => ({
  useHomeOptions: () => ({
    options: {
      year_list: [2022, 2023],
      date_list: [["January 2024", [["2024-01-15", "15"]]]],
    },
    error: null,
    loading: false,
  }),
}));

describe("Search", () => {
  it("frames browse by time with tabs and intro", () => {
    render(
      <MemoryRouter>
        <Search />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /browse jobs by time/i })).toBeInTheDocument();
    expect(screen.getByText(/Find Job/i)).toBeInTheDocument();
    expect(screen.getByText(/Extended search/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^calendar$/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /by year/i })).toBeInTheDocument();
  });
});
