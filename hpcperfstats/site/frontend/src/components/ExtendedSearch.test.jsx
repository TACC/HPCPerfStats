import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExtendedSearch from "./ExtendedSearch";
import * as useHomeOptionsModule from "../hooks/use-home-options";

vi.mock("../hooks/use-home-options", () => ({
  useHomeOptions: vi.fn(),
}));

function renderExtendedSearch(ui, { initialEntries = ["/jobs"] } = {}) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/jobs" element={ui} />
        <Route path="/job/:jid" element={<div data-testid="job-page">job</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ExtendedSearch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    useHomeOptionsModule.useHomeOptions.mockReturnValue({
      options: { metrics: [], queues: [], states: [] },
      error: null,
      loading: false,
    });
  });

  it("invokes onClose when Close is activated", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderExtendedSearch(<ExtendedSearch onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: /close extended search/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("navigates to job detail when Job ID is submitted", async () => {
    const user = userEvent.setup();
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);
    await user.type(screen.getByLabelText(/job id/i), "991");
    await user.click(screen.getByRole("button", { name: /^search$/i }));
    expect(await screen.findByTestId("job-page")).toBeInTheDocument();
  });

  it("shows loading state while options load", () => {
    useHomeOptionsModule.useHomeOptions.mockReturnValue({
      options: null,
      error: null,
      loading: true,
    });
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);
    expect(screen.getByText(/loading search options/i)).toBeInTheDocument();
  });
});
