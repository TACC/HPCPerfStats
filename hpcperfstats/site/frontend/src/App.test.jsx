import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import App from "./App";
import * as apiModule from "./api";

function renderApp(initialEntries = ["/"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <App />
    </MemoryRouter>
  );
}

describe("App", () => {
  beforeEach(() => {
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      pathname: "/",
      search: "",
      href: "",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading state while session is being fetched", () => {
    vi.spyOn(apiModule.api, "getSession").mockReturnValue(
      new Promise(() => {})
    );

    renderApp();
    expect(screen.getByText("Loading session…")).toBeInTheDocument();
  });

  it("redirects to login_prompt when not logged in", async () => {
    const hrefSpy = vi
      .spyOn(window.location, "href", "set")
      .mockImplementation(() => {});
    vi.spyOn(apiModule.api, "getSession").mockResolvedValue({
      logged_in: false,
    });

    renderApp();

    await waitFor(() => {
      expect(hrefSpy).toHaveBeenCalledWith("/login_prompt?next=%2F");
    });
  });

  it("renders routes when logged in", async () => {
    vi.spyOn(apiModule.api, "getSession").mockResolvedValue({
      logged_in: true,
      username: "test-user",
    });

    renderApp();

    await screen.findByText(/Search/i);
  });
});

