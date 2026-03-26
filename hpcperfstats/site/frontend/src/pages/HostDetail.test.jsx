import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import HostDetail from "./HostDetail";
import * as apiModule from "../api";
import { SessionContext } from "../session-context";

function renderHostDetail(path = "/host/node1.cluster/plot", session = { is_staff: false }) {
  return render(
    <SessionContext.Provider value={session}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/host/:host/plot" element={<HostDetail />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>
  );
}

describe("HostDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows plot unavailable message without details for non-staff", async () => {
    vi.spyOn(apiModule.api, "getHostPlot").mockResolvedValue({
      host: "node1.cluster",
      plot_item: null,
      plot_unavailable_reason:
        "No host plot data available for this host/time range.",
      end_time__gte: "2024-01-01T00:00:00Z",
      end_time__lte: "2024-01-02T00:00:00Z",
    });

    renderHostDetail();

    await waitFor(() => {
      expect(screen.getByText("Host: node1.cluster")).toBeInTheDocument();
    });
    expect(screen.getByText("Data not available.")).toBeInTheDocument();

    expect(screen.queryByLabelText("Show plot error details")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy Error Detail" })).not.toBeInTheDocument();
  });
});

