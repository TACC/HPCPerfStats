import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import AppPub from "./AppPub";
import * as apiModule from "./api";

vi.mock("./components/BokehEmbed.jsx", () => ({
  default: function BokehEmbedStub() {
    return <div data-testid="bokeh-stub">bokeh</div>;
  },
}));

const mockBundle = {
  status: "ready",
  machine_name: "cluster.test",
  sections: {},
};

function renderAppPub(initialEntry = "/") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/*" element={<AppPub />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AppPub", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("redirects the index route to cluster-dashboard", async () => {
    vi.spyOn(apiModule, "fetchPubClusterDashboard").mockResolvedValue(mockBundle);
    renderAppPub("/");
    await waitFor(() => {
      expect(screen.getByText("cluster.test")).toBeInTheDocument();
    });
  });

  it("renders cluster dashboard content after the pub bundle loads", async () => {
    vi.spyOn(apiModule, "fetchPubClusterDashboard").mockResolvedValue(mockBundle);
    renderAppPub("/cluster-dashboard");
    await waitFor(() => {
      expect(screen.getByText("cluster.test")).toBeInTheDocument();
    });
    expect(apiModule.fetchPubClusterDashboard).toHaveBeenCalledTimes(1);
  });

  it("surfaces fetch errors through the pub dashboard page", async () => {
    vi.spyOn(apiModule, "fetchPubClusterDashboard").mockRejectedValue(
      new Error("Pub API unavailable"),
    );
    renderAppPub("/cluster-dashboard");
    await waitFor(() => {
      expect(screen.getByText(/pub API unavailable/i)).toBeInTheDocument();
    });
  });
});
