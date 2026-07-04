import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Search from "../Search";
import * as useHomeOptionsModule from "../../hooks/use-home-options";
import { axeSeriousViolations } from "@test/vitest/axe-test-utils";

vi.mock("../../hooks/use-home-options", () => ({
  useHomeOptions: vi.fn(),
}));

function setHomeOptions(overrides = {}) {
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

  it("frames browse by time with tabs and intro", async () => {
    const view = render(
      <>
        <Search />
      </>,
    );
    expect(screen.getByRole("heading", { name: /browse jobs by time/i })).toBeInTheDocument();
    expect(screen.getByText(/Find Job/i)).toBeInTheDocument();
    expect(screen.getByText(/Extended search/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^calendar$/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /by year/i })).toBeInTheDocument();
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("shows loading while home options are fetched", () => {
    setHomeOptions({ loading: true, options: null });
    render(
      <>
        <Search />
      </>,
    );
    expect(screen.getByText(/^Loading…$/)).toBeInTheDocument();
  });

  it("shows an error banner when home options fail to load", () => {
    setHomeOptions({ error: "Home API unavailable", options: null });
    render(
      <>
        <Search />
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/home api unavailable/i);
  });

  it("defaults to the calendar tab and switches to year browse on request", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Search />
      </>,
    );

    expect(screen.getByRole("link", { name: /open jobs for january 2024, day 15/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "2022" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /by year/i }));
    expect(screen.getByRole("tab", { name: /by year/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("link", { name: "2022" })).toHaveAttribute("href", "/machine/year/2022");
    expect(screen.getByRole("link", { name: "2023" })).toHaveAttribute("href", "/machine/year/2023");
  });

  it("shows a no-data message when year and calendar lists are empty", () => {
    setHomeOptions({ options: { year_list: [], date_list: [] } });
    render(
      <>
        <Search />
      </>,
    );
    expect(screen.getAllByText(/no job data available/i).length).toBeGreaterThan(0);
  });

  it("links calendar days to date job list routes", () => {
    render(
      <>
        <Search />
      </>,
    );
    expect(
      screen.getByRole("link", { name: /open jobs for january 2024, day 15/i }),
    ).toHaveAttribute("href", "/machine/date/2024-01-15");
  });
});
