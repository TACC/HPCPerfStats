import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MachineRouteClient from "./machine-route-client";
import { configureNextNavigationFromPath } from "@/test-utils/next-navigation-state";

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
  it("renders Search on home pathname", () => {
    configureNextNavigationFromPath("/machine/");
    render(<MachineRouteClient />);
    expect(screen.getByTestId("view-search")).toBeInTheDocument();
  });

  it("renders JobList for date deep link pathname", () => {
    configureNextNavigationFromPath("/machine/date/2024-01-15/");
    render(<MachineRouteClient />);
    expect(screen.getByTestId("view-job-list")).toBeInTheDocument();
    expect(screen.queryByTestId("view-search")).not.toBeInTheDocument();
  });

  it("renders JobDetail for job id pathname", () => {
    configureNextNavigationFromPath("/machine/job/991/");
    render(<MachineRouteClient />);
    expect(screen.getByTestId("view-job-detail")).toBeInTheDocument();
  });
});
