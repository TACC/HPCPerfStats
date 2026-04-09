import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import TypeDetail from "./TypeDetail";
import * as apiModule from "../api";
import { SessionContext } from "../session-context";

function renderTypeDetail(path = "/job/12345/cpu", session = { is_staff: false }) {
  return render(
    <SessionContext.Provider value={session}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/job/:jid/:typeName" element={<TypeDetail />} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>
  );
}

describe("TypeDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows plot unavailable message without details for non-staff", async () => {
    vi.spyOn(apiModule.api, "getTypeDetail").mockResolvedValue({
      type_name: "cpu",
      jobid: "12345",
      tplot_item: null,
      tplot_unavailable_reason:
        "No device-level samples found for this job/type in host_data.",
      stats_data: [],
      schema: [],
    });

    renderTypeDetail();

    await waitFor(() => {
      expect(screen.getByText("Job 12345 / Type cpu")).toBeInTheDocument();
    });
    expect(screen.getByText("Unavailable — Data not available.")).toBeInTheDocument();

    expect(screen.queryByLabelText("Show plot error details")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy error detail" })).not.toBeInTheDocument();
  });
});

