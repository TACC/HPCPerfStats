import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PageMonthlyMetrics from "./PageMonthlyMetrics.jsx";

describe("PageMonthlyMetrics", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: "ready",
        schema_version: 1,
        sections: {
          expansion_factor: {
            monthly_daily_histograms: {},
            yearly_weekly_histograms: {},
          },
        },
      }),
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("loads anonymous dashboard bundle without session APIs", async () => {
    render(
      <MemoryRouter initialEntries={["/monthly-metrics"]}>
        <Routes>
          <Route path="monthly-metrics" element={<PageMonthlyMetrics />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/Monthly metrics/i)).toBeInTheDocument();
    });

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/pub/monthly-metrics/",
      expect.objectContaining({
        method: "GET",
        credentials: "omit",
      }),
    );
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 2, name: "Expansion factor" }),
      ).toBeInTheDocument();
    });
  });
});
