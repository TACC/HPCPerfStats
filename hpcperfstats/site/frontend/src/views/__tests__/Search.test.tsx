import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Search from "../Search";
import * as useHomeOptionsModule from "../../hooks/use-home-options";
import { axeSeriousViolations } from "@test/vitest/axe-test-utils";

vi.mock("../../hooks/use-home-options", () => ({
  useHomeOptions: vi.fn(),
}));

function buildDateList(count: number) {
  return Array.from({ length: count }, (_, i) => {
    const n = i + 1;
    const label = `Month ${n} 2024`;
    const dateStr = `2024-${String(n).padStart(2, "0")}-15`;
    return [label, [[dateStr, "15"]]] as [string, [string, string][]];
  });
}

function setHomeOptions(overrides: Record<string, unknown> = {}) {
  useHomeOptionsModule.useHomeOptions.mockReturnValue({
    options: {
      year_list: [2022, 2023],
      date_list: [["January 2024", [["2024-01-15", "15"]]]],
    },
    error: null,
    loading: false,
    ...overrides,
  });
}

describe("Search", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    setHomeOptions();
  });

  it("shows a single pane with years and calendar (no tabs)", async () => {
    const view = render(<Search />);
    expect(screen.getByRole("heading", { name: /browse jobs by time/i })).toBeInTheDocument();
    expect(screen.getByText(/Find Job/i)).toBeInTheDocument();
    expect(screen.getByText(/Extended search/i)).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /^calendar$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /by year/i })).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: /^years$/i })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: /^years$/i }).className).toMatch(
      /border-border/,
    );
    expect(screen.getByRole("navigation", { name: /^years$/i }).className).not.toMatch(
      /justify-end/,
    );
    expect(screen.getByRole("link", { name: "2022" })).toHaveAttribute("href", "/machine/year/2022");
    expect(screen.getByRole("link", { name: "2023" })).toHaveAttribute("href", "/machine/year/2023");
    expect(
      screen.getByRole("link", { name: /open jobs for january 2024, day 15/i }),
    ).toBeInTheDocument();
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("shows loading while home options are fetched", () => {
    setHomeOptions({ loading: true, options: null });
    render(<Search />);
    expect(screen.getByText(/^Loading…$/)).toBeInTheDocument();
  });

  it("shows an error banner when home options fail to load", () => {
    setHomeOptions({ error: "Home API unavailable", options: null });
    render(<Search />);
    expect(screen.getByRole("alert")).toHaveTextContent(/home api unavailable/i);
  });

  it("shows a no-data message when year and calendar lists are empty", () => {
    setHomeOptions({ options: { year_list: [], date_list: [] } });
    render(<Search />);
    expect(screen.getByText(/no job data available/i)).toBeInTheDocument();
  });

  it("links calendar days to date job list routes", () => {
    render(<Search />);
    expect(
      screen.getByRole("link", { name: /open jobs for january 2024, day 15/i }),
    ).toHaveAttribute("href", "/machine/date/2024-01-15");
  });

  it("expands visibility and scrolls when jumping to an unloaded month", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {});

    const date_list = buildDateList(14);
    setHomeOptions({ options: { year_list: [2024], date_list } });
    render(<Search />);

    expect(screen.getByRole("link", { name: "Month 1 2024" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Month 13 2024" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /load more months/i })).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: /jump to month/i }));
    await user.click(await screen.findByRole("option", { name: "Month 13 2024" }));

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Month 13 2024" })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalled();
    });
    const target = document.getElementById("search-month-Month-13-2024");
    expect(target).toBeTruthy();
    expect(scrollIntoView.mock.instances).toContain(target);
  });

  it("scrolls to an already-visible month without requiring load more", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {});

    render(<Search />);

    await user.click(screen.getByRole("combobox", { name: /jump to month/i }));
    await user.click(await screen.findByRole("option", { name: "January 2024" }));

    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalled();
    });
    const target = document.getElementById("search-month-January-2024");
    expect(target).toBeTruthy();
    expect(scrollIntoView.mock.instances).toContain(target);
  });
});
