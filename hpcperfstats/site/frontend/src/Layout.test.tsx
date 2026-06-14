import { useState, type ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { axeSeriousViolations } from "./axe-test-utils";
import Layout from "./Layout";
import { api } from "@/api";

const mutateAsync = vi.fn();

vi.mock("./hooks/use-home-options", () => ({
  useHomeOptions: vi.fn(() => ({
    options: { metrics: [], queues: [], states: [] },
    error: null,
    loading: false,
  })),
}));

vi.mock("@/api/generated/session/session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/generated/session/session")>();
  return {
    ...actual,
    useSessionDropStaffCreate: () => ({
      mutateAsync,
      isPending: false,
    }),
  };
});

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function renderLayout(session, onSessionChange = vi.fn()) {
  return renderWithProviders(
    <Layout session={session} onSessionChange={onSessionChange}>
      <div>Child content</div>
    </Layout>,
  );
}

function SessionHarness() {
  const [session, setSession] = useState({
    logged_in: true,
    username: "alice",
    is_staff: true,
  });

  return (
    <Layout session={session} onSessionChange={setSession}>
      <div>Child content</div>
    </Layout>
  );
}

describe("Layout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mutateAsync.mockReset();
  });

  it("shows staff actions menu only for staff sessions", async () => {
    const user = userEvent.setup();
    const firstRender = renderLayout({
      logged_in: true,
      username: "alice",
      is_staff: true,
    });
    await user.click(screen.getByRole("button", { name: "Staff actions" }));
    expect(await screen.findByText("HPCPerfStats Monitor")).toBeInTheDocument();
    firstRender.unmount();

    renderLayout({ logged_in: true, username: "bob", is_staff: false });
    expect(
      screen.queryByRole("button", { name: "Staff actions" }),
    ).not.toBeInTheDocument();
  });

  it("shows all four staff menu items above page chrome", async () => {
    const user = userEvent.setup();
    renderLayout({
      logged_in: true,
      username: "alice",
      is_staff: true,
    });
    await user.click(screen.getByRole("button", { name: "Staff actions" }));
    const items = await screen.findAllByRole("menuitem");
    expect(items).toHaveLength(4);
    expect(items.map((item) => item.textContent?.trim())).toEqual([
      "Job Failure Monitor",
      "HPCPerfStats Monitor",
      "Disable Staff Permissions",
      "Invalidate Cache For Page",
    ]);
    for (const item of items) {
      expect(item).toBeVisible();
    }
  });

  it("demotes staff session and displays login notice", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mutateAsync.mockResolvedValue({
      ok: true,
      message:
        "Staff access removed for this session. Log out and log back in to restore staff access.",
      is_staff: false,
    });
    vi.spyOn(api, "getSession").mockResolvedValue({
      logged_in: true,
      username: "alice",
      is_staff: false,
    });

    const user = userEvent.setup();
    renderWithProviders(<SessionHarness />);

    await user.click(screen.getByRole("button", { name: "Staff actions" }));
    await user.click(await screen.findByText("Disable Staff Permissions"));

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(api.getSession).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText(
        "Staff access removed for this session. Log out and log back in to restore staff access.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Staff actions" }),
    ).not.toBeInTheDocument();
  });

  it("exposes skip link to main content", async () => {
    const view = renderLayout({ logged_in: true, username: "alice", is_staff: false });
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("links to API key management page", () => {
    renderLayout({ logged_in: true, username: "alice", is_staff: false });
    expect(screen.getByRole("link", { name: "API key" })).toHaveAttribute("href", "/machine/api-key");
  });

  it("has no serious axe violations with extended search open", async () => {
    const user = userEvent.setup();
    const view = renderLayout({ logged_in: true, username: "alice", is_staff: false });
    await user.click(screen.getByRole("button", { name: /extended search/i }));
    expect(await screen.findByRole("dialog", { name: /extended search/i })).toBeInTheDocument();
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });
});
