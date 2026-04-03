import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import Layout from "./Layout";
import { api } from "./api";

function renderLayout(session, onSessionChange = vi.fn()) {
  return render(
    <MemoryRouter>
      <Layout session={session} onSessionChange={onSessionChange}>
        <div>Child content</div>
      </Layout>
    </MemoryRouter>,
  );
}

function SessionHarness() {
  const [session, setSession] = useState({
    logged_in: true,
    username: "alice",
    is_staff: true,
  });

  return (
    <MemoryRouter>
      <Layout session={session} onSessionChange={setSession}>
        <div>Child content</div>
      </Layout>
    </MemoryRouter>
  );
}

describe("Layout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows staff actions menu only for staff sessions", async () => {
    const user = userEvent.setup();
    const firstRender = renderLayout({
      logged_in: true,
      username: "alice",
      is_staff: true,
    });
    await user.click(screen.getByRole("button", { name: "Staff actions" }));
    expect(screen.getByRole("menuitem", { name: "HPCPerfStats Monitor" })).toBeInTheDocument();
    firstRender.unmount();

    renderLayout({ logged_in: true, username: "bob", is_staff: false });
    expect(
      screen.queryByRole("button", { name: "Staff actions" }),
    ).not.toBeInTheDocument();
  });

  it("demotes staff session and displays login notice", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(api, "dropStaffForSession").mockResolvedValue({
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
    render(<SessionHarness />);

    await user.click(screen.getByRole("button", { name: "Staff actions" }));
    await user.click(
      screen.getByRole("menuitem", { name: "Disable Staff Permissions" }),
    );

    expect(api.dropStaffForSession).toHaveBeenCalledTimes(1);
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

  it("exposes skip link to main content", () => {
    renderLayout({ logged_in: true, username: "alice", is_staff: false });
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
  });
});
