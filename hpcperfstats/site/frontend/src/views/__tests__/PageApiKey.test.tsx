import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { axeSeriousViolations } from "../../axe-test-utils";
import PageApiKey from "../PageApiKey";
import { SessionContext } from "../../session-context";
import { useUserApiKey } from "@/hooks/use-user-api-key";

vi.mock("@/hooks/use-user-api-key");

function renderPage(session = { username: "tester", is_staff: false }) {
  return render(
    <SessionContext.Provider value={session}>
      <PageApiKey />
    </SessionContext.Provider>,
  );
}

describe("PageApiKey", () => {
  const rotate = vi.fn();

  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    rotate.mockReset();
    vi.mocked(useUserApiKey).mockReturnValue({
      data: null,
      error: null,
      loading: true,
      refetch: vi.fn(),
      rotate,
      rotating: false,
      rotateError: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows existing key prefix when raw_key is absent", async () => {
    vi.mocked(useUserApiKey).mockReturnValue({
      data: {
        username: "alice",
        raw_key: null,
        key_prefix: "abc123def456",
      },
      error: null,
      loading: false,
      refetch: vi.fn(),
      rotate,
      rotating: false,
      rotateError: null,
    });

    const view = renderPage();

    expect(await screen.findByText(/HPCPerfStats API key/i)).toBeInTheDocument();
    expect(screen.getByText(/Signed in as:/)).toBeInTheDocument();
    expect(screen.getByText("abc123def456")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy API key" })).not.toBeInTheDocument();
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("shows copy control when a raw key is returned", async () => {
    vi.mocked(useUserApiKey).mockReturnValue({
      data: {
        username: "alice",
        raw_key: "deadbeefcafe0123",
        key_prefix: "deadbeefcafe",
      },
      error: null,
      loading: false,
      refetch: vi.fn(),
      rotate,
      rotating: false,
      rotateError: null,
    });

    renderPage();

    expect(await screen.findByText("deadbeefcafe0123")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy API key" })).toBeInTheDocument();
  });

  it("calls rotate endpoint after confirm and displays new key", async () => {
    let currentData = {
      username: "alice",
      raw_key: null as string | null,
      key_prefix: "oldprefix12",
    };
    rotate.mockImplementation(async () => {
      currentData = {
        username: "alice",
        raw_key: "newrawkeyvalue",
        key_prefix: "newrawkeyva",
      };
    });
    vi.mocked(useUserApiKey).mockImplementation(() => ({
      data: currentData,
      error: null,
      loading: false,
      refetch: vi.fn(),
      rotate,
      rotating: false,
      rotateError: null,
    }));

    const user = userEvent.setup();
    const view = renderPage();

    await screen.findByText("oldprefix12");

    await user.click(screen.getByRole("button", { name: /Invalidate and Create New Key/i }));

    await waitFor(() => {
      expect(rotate).toHaveBeenCalled();
    });
    view.rerender(
      <SessionContext.Provider value={{ username: "tester", is_staff: false }}>
        <PageApiKey />
      </SessionContext.Provider>,
    );
    expect(await screen.findByText("newrawkeyvalue")).toBeInTheDocument();
  });
});
