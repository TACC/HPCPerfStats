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
  states: ["COMPLETED", "RUNNING"],
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
    searchParams = new URLSearchParams();
    pathname = "/machine/jobs/";
  });

  it("toggles queue chip on and off via router.replace", async () => {
    const user = userEvent.setup();
    render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    await user.click(screen.getByRole("button", { name: "normal" }));
    expect(replace).toHaveBeenCalledTimes(1);
    let href = replace.mock.calls[0]?.[0] as string;
    expect(href).toContain("queue=normal");

    searchParams = new URLSearchParams("queue=normal");
    render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );
    await user.click(screen.getAllByRole("button", { name: "normal" })[1]);
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

    await user.click(screen.getByRole("button", { name: "debug" }));
    expect(replace).toHaveBeenCalledTimes(1);
    const href = replace.mock.calls[0]?.[0] as string;
    expect(href).toContain("queue=normal");
    expect(href).toContain("debug");
  });

  it("clears a dimension when Clear is clicked", async () => {
    const user = userEvent.setup();
    searchParams = new URLSearchParams("state=COMPLETED,RUNNING");
    render(
      <JobListHeaderFilters filterOptions={filterOptions} routeParams={{}} />,
    );

    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(replace).toHaveBeenCalledTimes(1);
    const href = replace.mock.calls[0]?.[0] as string;
    expect(href).not.toContain("state=");
  });
});
