import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { axeSeriousViolations } from "@test/vitest/axe-test-utils";
import PageTestLogin from "../PageTestLogin";
import { useTestLoginUser } from "@/hooks/use-test-login-user";

vi.mock("@/hooks/use-test-login-user");

describe("PageTestLogin", () => {
  const save = vi.fn();

  beforeEach(() => {
    save.mockReset();
    vi.mocked(useTestLoginUser).mockReturnValue({
      data: null,
      error: null,
      loading: true,
      refetch: vi.fn(),
      save,
      saving: false,
      saveError: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a not-available state when the API is hidden", async () => {
    vi.mocked(useTestLoginUser).mockReturnValue({
      data: null,
      error: "Not found",
      loading: false,
      refetch: vi.fn(),
      save,
      saving: false,
      saveError: null,
    });
    const view = render(<PageTestLogin />);
    expect(screen.getByText(/Test login is not available/i)).toBeInTheDocument();
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("shows the configured username and login URL", async () => {
    vi.mocked(useTestLoginUser).mockReturnValue({
      data: {
        configured: true,
        username: "qa",
        login_url: "/test-login/",
      },
      error: null,
      loading: false,
      refetch: vi.fn(),
      save,
      saving: false,
      saveError: null,
    });
    const view = render(<PageTestLogin />);
    expect(screen.getByText("qa")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "/test-login/" })).toHaveAttribute(
      "href",
      "/test-login/",
    );
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("submits username and password through the thin hook", async () => {
    const user = userEvent.setup();
    save.mockResolvedValue({});
    vi.mocked(useTestLoginUser).mockReturnValue({
      data: { configured: false, username: null, login_url: "/test-login/" },
      error: null,
      loading: false,
      refetch: vi.fn(),
      save,
      saving: false,
      saveError: null,
    });
    render(<PageTestLogin />);
    await user.type(screen.getByLabelText("Username"), "qa");
    await user.type(screen.getByLabelText("Password"), "secret12");
    await user.click(screen.getByRole("button", { name: "Save test user" }));
    expect(save).toHaveBeenCalledWith("qa", "secret12");
  });
});
