import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MachineRouteClient, { matchMachineView } from "./machine-route-client";
import { configureNextNavigationFromPath } from "@test/vitest/test-utils/next-navigation-state";
import type { MachineRouteView } from "@/utils/machine-route-params";

vi.mock("@/views/Search", () => ({
  default: () => <div data-testid="view-search">Search</div>,
}));
vi.mock("@/views/JobList", () => ({
  default: () => <div data-testid="view-job-list">JobList</div>,
}));
vi.mock("@/views/JobDetail", () => ({
  default: () => <div data-testid="view-job-detail">JobDetail</div>,
}));
vi.mock("@/views/TypeDetail", () => ({
  default: () => <div data-testid="view-type-detail">TypeDetail</div>,
}));
vi.mock("@/views/HostDetail", () => ({
  default: () => <div data-testid="view-host-detail">HostDetail</div>,
}));
vi.mock("@/views/AdminMonitor", () => ({
  default: () => <div data-testid="view-admin-monitor">AdminMonitor</div>,
}));
vi.mock("@/views/JobMonitor", () => ({
  default: () => <div data-testid="view-job-monitor">JobMonitor</div>,
}));
vi.mock("@/views/PageApiKey", () => ({
  default: () => <div data-testid="view-api-key">PageApiKey</div>,
}));
vi.mock("@/views/PageNotFound", () => ({
  default: () => <div data-testid="view-not-found">PageNotFound</div>,
}));

describe("MachineRouteClient", () => {
  it("renders Search on home pathname", async () => {
    configureNextNavigationFromPath("/machine/");
    render(<MachineRouteClient />);
    expect(await screen.findByTestId("view-search")).toBeInTheDocument();
  });

  it("renders JobList for date deep link pathname", async () => {
    configureNextNavigationFromPath("/machine/date/2024-01-15/");
    render(<MachineRouteClient />);
    expect(await screen.findByTestId("view-job-list")).toBeInTheDocument();
    expect(screen.queryByTestId("view-search")).not.toBeInTheDocument();
  });

  it("renders JobDetail for job id pathname", async () => {
    configureNextNavigationFromPath("/machine/job/991/");
    render(<MachineRouteClient />);
    expect(await screen.findByTestId("view-job-detail")).toBeInTheDocument();
  });
});

const ROUTED_VIEWS: { view: MachineRouteView; testId: string }[] = [
  { view: "search", testId: "view-search" },
  { view: "jobList", testId: "view-job-list" },
  { view: "jobDetail", testId: "view-job-detail" },
  { view: "typeDetail", testId: "view-type-detail" },
  { view: "hostDetail", testId: "view-host-detail" },
  { view: "adminMonitor", testId: "view-admin-monitor" },
  { view: "jobMonitor", testId: "view-job-monitor" },
  { view: "pageApiKey", testId: "view-api-key" },
];

describe("matchMachineView", () => {
  it.each(ROUTED_VIEWS)("maps $view to the expected view component", async ({ view, testId }) => {
    render(matchMachineView(view));
    expect(await screen.findByTestId(testId)).toBeInTheDocument();
  });

  it("falls back to PageNotFound for unknown views", async () => {
    render(matchMachineView("notFound"));
    expect(await screen.findByTestId("view-not-found")).toBeInTheDocument();
  });
});
