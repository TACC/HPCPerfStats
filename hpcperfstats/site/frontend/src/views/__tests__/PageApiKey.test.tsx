import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { axeSeriousViolations } from "../../axe-test-utils";
import PageApiKey from "../PageApiKey";
import { SessionContext } from "../../session-context";

function renderPage(session = { username: "tester", is_staff: false }) {
  return render(
    <SessionContext.Provider value={session}>
      <PageApiKey />
    </SessionContext.Provider>,
  );
}

describe("PageApiKey", () => {
  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows existing key prefix when raw_key is absent", async () => {
    const { api } = await import("@/api");
    vi.spyOn(api, "getUserApiKey").mockResolvedValue({
      username: "alice",
      raw_key: null,
      key_prefix: "abc123def456",
    });

    const view = renderPage();

    expect(await screen.findByText(/HPCPerfStats API key/i)).toBeInTheDocument();
    expect(screen.getByText(/Signed in as:/)).toBeInTheDocument();
    expect(screen.getByText("abc123def456")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy API key" })).not.toBeInTheDocument();
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("shows copy control when a raw key is returned", async () => {
    const { api } = await import("@/api");
    vi.spyOn(api, "getUserApiKey").mockResolvedValue({
      username: "alice",
      raw_key: "deadbeefcafe0123",
      key_prefix: "deadbeefcafe",
    });

    renderPage();

    expect(await screen.findByText("deadbeefcafe0123")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy API key" })).toBeInTheDocument();
  });

  it("calls rotate endpoint after confirm and displays new key", async () => {
    const { api } = await import("@/api");
    vi.spyOn(api, "getUserApiKey").mockResolvedValue({
      username: "alice",
      raw_key: null,
      key_prefix: "oldprefix12",
    });
    const rotate = vi.spyOn(api, "rotateUserApiKey").mockResolvedValue({
      username: "alice",
      raw_key: "newrawkeyvalue",
      key_prefix: "newrawkeyva",
    });

    const user = userEvent.setup();
    renderPage();

    await screen.findByText("oldprefix12");

    await user.click(screen.getByRole("button", { name: /Invalidate and Create New Key/i }));

    await waitFor(() => {
      expect(rotate).toHaveBeenCalled();
    });
    expect(await screen.findByText("newrawkeyvalue")).toBeInTheDocument();
  });
});
