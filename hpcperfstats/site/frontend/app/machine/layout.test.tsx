import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import MachineLayout from "./layout";

vi.mock("@/patch-resize-observer-for-bokeh", () => ({
  applyBokehResizeObserverDeferral: vi.fn(),
}));

vi.mock("@/Layout", () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="layout-shell">{children}</div>
  ),
}));

const useSessionRetrieveMock = vi.fn();

vi.mock("@/api/generated/session/session", () => ({
  useSessionRetrieve: () => useSessionRetrieveMock(),
  getSessionRetrieveQueryKey: () => ["/api/session/"],
}));

function renderMachineLayout(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MachineLayout>{children}</MachineLayout>
    </QueryClientProvider>,
  );
}

describe("MachineLayout", () => {
  beforeEach(() => {
    useSessionRetrieveMock.mockReset();
  });

  it("renders children while session is loading", () => {
    useSessionRetrieveMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });

    renderMachineLayout(<p>page body</p>);

    expect(screen.getByText("page body")).toBeInTheDocument();
    const status = screen.getByText("Loading session…");
    expect(status).toHaveClass("sr-only");
    expect(status).toHaveAttribute("role", "status");
    expect(screen.queryByText("Loading session…", { selector: "div.mx-auto" })).not.toBeInTheDocument();
  });

  it("renders children after session resolves", () => {
    useSessionRetrieveMock.mockReturnValue({
      data: {
        logged_in: true,
        username: "alice",
        is_staff: false,
        machine_name: "cluster.test",
      },
      isLoading: false,
      isError: false,
    });

    renderMachineLayout(<p>loaded page</p>);

    expect(screen.getByText("loaded page")).toBeInTheDocument();
    expect(screen.queryByText("Loading session…")).not.toBeInTheDocument();
  });
});
