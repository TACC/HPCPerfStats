import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import HostDetail from "./HostDetail";
import * as apiModule from "../api";

function renderHostDetail(path = "/host/node1.cluster/plot") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/host/:host/plot" element={<HostDetail />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("HostDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("passes unavailable reason to BokehEmbed details popup and copy button", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

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
    expect(screen.getByText("Plot not available")).toBeInTheDocument();

    const detailsTrigger = screen.getByLabelText("Show plot error details");
    fireEvent.mouseEnter(detailsTrigger);

    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "No host plot data available for this host/time range."
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy Error Detail" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(
        "No host plot data available for this host/time range."
      );
    });
  });
});

