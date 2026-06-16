import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import JobListHeaderFilters from "./JobListHeaderFilters";

const replace = vi.fn();
let searchParams = new URLSearchParams();
let pathname = "/machine/jobs/";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => pathname,
  useSearchParams: () => searchParams,
}));

const filterOptions = {
  usernames: ["alice", "bob"],
  accounts: ["projA"],
  queues: ["normal", "debug"],
  states: ["completed", "canceled", "timeout"],
  performance_statuses: [
    { sort_rank: 0, label: "Summary available" },
    { sort_rank: 2, label: "Monitoring gaps" },
  ],
  truncated: {
    usernames: false,
    accounts: false,
    queues: false,
    states: false,
  },
};

describe("JobListHeaderFilters", () => {
  beforeEach(() => {
    replace.mockClear();
    replace.mockImplementation((href: string) => {
      const qIndex = href.indexOf("?");
      searchParams = new URLSearchParams(qIndex >= 0 ? href.slice(qIndex + 1) : "");
    });
    searchParams = new URLSearchParams();
    pathname = "/machine/jobs/";
  });

  it("starts collapsed and expands to show filter chips", async () => {
    const user = userEvent.setup();
    render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    expect(screen.queryByRole("button", { name: "normal" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /refine this list/i }));

    expect(screen.getByRole("button", { name: "normal" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Completed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Timeout" })).toBeInTheDocument();
  });

  it("shows active filter count when collapsed", () => {
    searchParams = new URLSearchParams("queue=normal&state=completed");
    render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    expect(screen.getByText("(2)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "normal" })).not.toBeInTheDocument();
  });

  it("toggles queue chip on and off via router.replace", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    await user.click(screen.getByRole("button", { name: /refine this list/i }));
    await user.click(screen.getByRole("button", { name: "normal" }));
    expect(replace).toHaveBeenCalledTimes(1);
    let href = replace.mock.calls[0]?.[0] as string;
    expect(href).toContain("queue=normal");

    searchParams = new URLSearchParams("queue=normal");
    rerender(<JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />);
    if (!screen.queryByRole("button", { name: "normal" })) {
      await user.click(screen.getByRole("button", { name: /refine this list/i }));
    }
    await user.click(screen.getByRole("button", { name: "normal" }));
    expect(replace).toHaveBeenCalledTimes(2);
    href = replace.mock.calls[1]?.[0] as string;
    expect(href).not.toContain("queue=");
  });

  it("adds second queue for multi-select OR", async () => {
    const user = userEvent.setup();
    searchParams = new URLSearchParams("queue=normal");
    render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    await user.click(screen.getByRole("button", { name: /refine this list/i }));
    await user.click(screen.getByRole("button", { name: "debug" }));
    expect(replace).toHaveBeenCalledTimes(1);
    const href = replace.mock.calls[0]?.[0] as string;
    expect(href).toContain("queue=normal");
    expect(href).toContain("debug");
  });

  it("clears a dimension when Clear is clicked", async () => {
    const user = userEvent.setup();
    searchParams = new URLSearchParams("state=completed,canceled");
    render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    await user.click(screen.getByRole("button", { name: /refine this list/i }));
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(replace).toHaveBeenCalledTimes(1);
    const href = replace.mock.calls[0]?.[0] as string;
    expect(href).not.toContain("state=");
  });

  it("double-click Clear header filters triggers a single navigation", async () => {
    const user = userEvent.setup();
    searchParams = new URLSearchParams("queue=normal&state=completed");
    const { rerender } = render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    await user.click(screen.getByRole("button", { name: /refine this list/i }));
    const clearButton = screen.getByRole("button", { name: /clear header filters/i });
    await user.click(clearButton);
    expect(replace).toHaveBeenCalledTimes(1);

    rerender(<JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />);
    await user.click(clearButton);
    expect(replace).toHaveBeenCalledTimes(1);
  });

  it("shows dimension labels while filter options are loading", async () => {
    const user = userEvent.setup();
    render(
      <JobListHeaderFilters filterOptions={null} optionsLoading routeParams={{}} />,
    );

    await user.click(screen.getByRole("button", { name: /refine this list/i }));

    expect(screen.getByText("Performance data")).toBeInTheDocument();
    expect(screen.getByText("User")).toBeInTheDocument();
    expect(screen.getByText("Project")).toBeInTheDocument();
    expect(screen.getByText("Queue")).toBeInTheDocument();
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
