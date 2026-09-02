import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useHomeOptionsQuery } from "@/hooks/use-home-options";
import MachineLayout from "./layout";

vi.mock("@/patch-resize-observer-for-bokeh", () => ({
  applyBokehResizeObserverDeferral: vi.fn(),
}));

vi.mock("@/Layout", () => ({
  default: ({
    children,
    session,
  }: {
    children: React.ReactNode;
    session: { separate_test_login?: boolean };
  }) => (
    <div
      data-testid="layout-shell"
      data-separate-test-login={String(Boolean(session.separate_test_login))}
    >
      {children}
    </div>
  ),
}));

const useSessionRetrieveMock = vi.fn();
const authenticatedChildHookSpy = vi.fn();

vi.mock("@/api/generated/session/session", () => ({
  useSessionRetrieve: () => useSessionRetrieveMock(),
  getSessionRetrieveQueryKey: () => ["/api/session/"],
}));

vi.mock("@/hooks/use-home-options", () => ({
  useHomeOptionsQuery: () => {
    authenticatedChildHookSpy();
    return { data: null, error: null, loading: false };
  },
}));

function AuthenticatedPageStub() {
  useHomeOptionsQuery();
  return <p>page body</p>;
}

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
    authenticatedChildHookSpy.mockReset();
  });

  it("does not render children while session is loading", () => {
    useSessionRetrieveMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });

    renderMachineLayout(<AuthenticatedPageStub />);

    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByText("Loading session…")).toBeInTheDocument();
    expect(authenticatedChildHookSpy).not.toHaveBeenCalled();
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

    renderMachineLayout(<AuthenticatedPageStub />);

    expect(screen.getByText("page body")).toBeInTheDocument();
    expect(screen.queryByText("Loading session…")).not.toBeInTheDocument();
    expect(authenticatedChildHookSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("layout-shell")).toHaveAttribute(
      "data-separate-test-login",
      "false",
    );
  });

  it("forwards separate_test_login from the session API to Layout", () => {
    useSessionRetrieveMock.mockReturnValue({
      data: {
        logged_in: true,
        username: "alice",
        is_staff: true,
        machine_name: "cluster.test",
        separate_test_login: true,
      },
      isLoading: false,
      isError: false,
    });

    renderMachineLayout(<AuthenticatedPageStub />);

    expect(screen.getByTestId("layout-shell")).toHaveAttribute(
      "data-separate-test-login",
      "true",
    );
  });
});
