import { useState } from "react";
import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { axeSeriousViolations } from "./axe-test-utils";
import Layout from "./Layout";
import { getSessionRetrieveQueryKey } from "@/api/generated/session/session";
import {
  createTestQueryClient,
  renderWithProviders,
} from "./test-utils/render-with-providers";
import { nextNavigationMock } from "./test-utils/next-navigation-state";

const mutateAsync = vi.fn();
const invalidateMutateAsync = vi.fn();

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

vi.mock("@/api/generated/admin/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/generated/admin/admin")>();
  return {
    ...actual,
    useCacheInvalidatePageCreate: () => ({
      mutateAsync: invalidateMutateAsync,
      isPending: false,
    }),
  };
});

vi.mock("@/config/site-identity", () => ({
  SITE_MACHINE_NAME: "Fractal",
}));

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
    invalidateMutateAsync.mockReset();
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

    const queryClient = createTestQueryClient();
    const fetchQuerySpy = vi.spyOn(queryClient, "fetchQuery").mockResolvedValue({
      logged_in: true,
      username: "alice",
      is_staff: false,
    });

    const user = userEvent.setup();
    renderWithProviders(<SessionHarness />, { queryClient });

    await user.click(screen.getByRole("button", { name: "Staff actions" }));
    await user.click(await screen.findByText("Disable Staff Permissions"));

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(fetchQuerySpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: getSessionRetrieveQueryKey() }),
    );
    expect(
      await screen.findByText(
        "Staff access removed for this session. Log out and log back in to restore staff access.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Staff actions" }),
    ).not.toBeInTheDocument();
  });

  it("does not render a skip link to main content", async () => {
    const view = renderLayout({ logged_in: true, username: "alice", is_staff: false });
    expect(
      screen.queryByRole("link", { name: /skip to main content/i }),
    ).not.toBeInTheDocument();
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
    const backdrop = screen.getByTestId("extended-search-backdrop");
    expect(backdrop).toHaveClass("overflow-y-auto");
    expect(document.querySelector('[data-slot="dialog"]')).toBeNull();
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("links header home controls to /machine/ not site root", () => {
    renderLayout({ logged_in: true, username: "alice", is_staff: false });
    const homeLinks = screen.getAllByRole("link", { name: /hpcperfstats/i });
    expect(homeLinks.length).toBeGreaterThan(0);
    for (const link of homeLinks) {
      const href = link.getAttribute("href");
      expect(href).toMatch(/^\/machine\/?$/);
      expect(href).not.toBe("/");
    }
  });

  it("closes extended search on Escape and restores focus to toggle", async () => {
    const user = userEvent.setup();
    renderLayout({ logged_in: true, username: "alice", is_staff: false });
    const toggle = screen.getByRole("button", { name: /extended search/i });
    await user.click(toggle);
    expect(await screen.findByRole("dialog", { name: /extended search/i })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /extended search/i })).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(toggle).toHaveFocus();
    });
  });

  it("closes extended search when backdrop is clicked", async () => {
    const user = userEvent.setup();
    renderLayout({ logged_in: true, username: "alice", is_staff: false });
    await user.click(screen.getByRole("button", { name: /extended search/i }));
    expect(await screen.findByRole("dialog", { name: /extended search/i })).toBeInTheDocument();

    await user.click(screen.getByTestId("extended-search-backdrop"));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /extended search/i })).not.toBeInTheDocument();
    });
  });

  it("closes extended search when pathname changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Layout session={{ logged_in: true, username: "alice", is_staff: false }}>
        <div>Child content</div>
      </Layout>,
      { withNavigationSync: true, initialPath: "/machine/jobs/" },
    );
    await user.click(screen.getByRole("button", { name: /extended search/i }));
    expect(await screen.findByRole("dialog", { name: /extended search/i })).toBeInTheDocument();

    await act(async () => {
      await nextNavigationMock.router.push("/machine/");
    });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /extended search/i })).not.toBeInTheDocument();
    });
  });

  it("closes extended search when searchParams change on the same pathname", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Layout session={{ logged_in: true, username: "alice", is_staff: false }}>
        <div>Child content</div>
      </Layout>,
      {
        withNavigationSync: true,
        initialPath: "/machine/jobs/?order_by=-end_time",
      },
    );
    await user.click(screen.getByRole("button", { name: /extended search/i }));
    expect(await screen.findByRole("dialog", { name: /extended search/i })).toBeInTheDocument();

    await act(async () => {
      await nextNavigationMock.router.push("/machine/jobs/?order_by=username");
    });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /extended search/i })).not.toBeInTheDocument();
    });
  });

  it("shows build-time cluster name when session machine_name is empty", () => {
    renderLayout({
      logged_in: true,
      username: "alice",
      is_staff: false,
      machine_name: "",
    });
    expect(screen.getByText("Fractal")).toBeInTheDocument();
  });

  it("prefers session machine_name over build-time identity", () => {
    renderLayout({
      logged_in: true,
      username: "alice",
      is_staff: false,
      machine_name: "Live Cluster",
    });
    expect(screen.getByText("Live Cluster")).toBeInTheDocument();
    expect(screen.queryByText("Fractal")).not.toBeInTheDocument();
  });

  it("hides staff actions while placeholder session has is_staff false", () => {
    renderLayout({
      logged_in: true,
      username: "",
      is_staff: false,
      machine_name: "",
    });
    expect(screen.queryByRole("button", { name: "Staff actions" })).not.toBeInTheDocument();
  });
});
