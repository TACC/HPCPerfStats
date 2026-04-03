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
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /skip to main content/i })).toBeInTheDocument();
  });

  it("redirects to login_prompt when not logged in", async () => {
    const replaceSpy = vi
      .spyOn(window.location, "replace")
      .mockImplementation(() => {});
    vi.spyOn(apiModule.api, "getSession").mockResolvedValue({
      logged_in: false,
    });

    renderApp();

    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/login_prompt?next=%2F");
    });
  });

  it("renders routes when logged in", async () => {
    vi.spyOn(apiModule.api, "getSession").mockResolvedValue({
      logged_in: true,
      username: "test-user",
    });
    vi.spyOn(apiModule.api, "getHomeOptions").mockResolvedValue({
      year_list: [],
      date_list: [],
      metrics: [],
      queues: [],
      states: [],
    });

    renderApp();

    await screen.findByRole("heading", { name: /search jobs/i });
  });
});

