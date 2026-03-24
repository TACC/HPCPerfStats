import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import TypeDetail from "./TypeDetail";
import * as apiModule from "../api";

function renderTypeDetail(path = "/job/12345/cpu") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/job/:jid/:typeName" element={<TypeDetail />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("TypeDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows unavailable details for missing type plot and supports copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    vi.spyOn(apiModule.api, "getTypeDetail").mockResolvedValue({
      type_name: "cpu",
      jobid: "12345",
      tscript: "",
      tdiv: "",
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
    expect(screen.getByText("Plot not available")).toBeInTheDocument();

    const detailsTrigger = screen.getByLabelText("Show plot error details");
    fireEvent.mouseEnter(detailsTrigger.parentElement);
    await waitFor(() => {
      expect(screen.getByRole("tooltip")).toHaveTextContent(
        "No device-level samples found for this job/type in host_data."
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "Copy Error Detail" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(
        "No device-level samples found for this job/type in host_data."
      );
    });
  });
});

