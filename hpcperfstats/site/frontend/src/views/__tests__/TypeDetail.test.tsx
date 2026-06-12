import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import TypeDetail from "../TypeDetail";
import * as apiModule from "@/api";
import { SessionContext } from "../../session-context";
import { configureNextNavigationFromPath } from "../../test-utils/next-navigation-state";

vi.mock("../bokehInit", () => ({
  ensureBokehLoaded: vi.fn(() => Promise.resolve(globalThis.window?.Bokeh)),
}));

function renderTypeDetail(path = "/job/12345/cpu", session = { is_staff: false }) {
  configureNextNavigationFromPath(path);
  return render(
    <SessionContext.Provider value={session}>
      <TypeDetail />
    </SessionContext.Provider>,
  );
}

describe("TypeDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.Bokeh;
  });

  it("shows loading while type detail is fetched", () => {
    vi.spyOn(apiModule.api, "getTypeDetail").mockReturnValue(new Promise(() => {}));
    renderTypeDetail();
    expect(screen.getByText(/loading type detail/i)).toBeInTheDocument();
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

  it("shows a banner when the API request fails", async () => {
    vi.spyOn(apiModule.api, "getTypeDetail").mockRejectedValue(new Error("Type detail failed"));
    renderTypeDetail();
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/type detail failed/i);
    });
  });

  it("embeds the type plot and renders counter stats when data is available", async () => {
    const embedItem = vi.fn(() => ({
      roots: [{ model: { document: { is_idle: true, idle: { connect: vi.fn(), disconnect: vi.fn() } } } }],
    }));
    window.Bokeh = { embed: { embed_item: embedItem } };

    vi.spyOn(apiModule.api, "getTypeDetail").mockResolvedValue({
      type_name: "cpu",
      jobid: "12345",
      tplot_item: { doc: {}, root_ids: ["type-root"] },
      tplot_unavailable_reason: null,
      stats_data: [["t0", [10, 20]]],
      schema: ["ctr_a", "ctr_b"],
    });

    renderTypeDetail();

    await waitFor(() => {
      expect(embedItem).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole("columnheader", { name: "ctr_a" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "ctr_b" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "t0" })).toBeInTheDocument();
    expect(screen.getByText("10.00")).toBeInTheDocument();
    expect(screen.getByText("20.00")).toBeInTheDocument();
  });

  it("shows staff plot error detail controls when the plot is unavailable", async () => {
    vi.spyOn(apiModule.api, "getTypeDetail").mockResolvedValue({
      type_name: "cpu",
      jobid: "12345",
      tplot_item: null,
      tplot_unavailable_reason:
        "No device-level samples found for this job/type in host_data.",
      stats_data: [],
      schema: [],
    });

    renderTypeDetail("/job/12345/cpu", { is_staff: true });

    await waitFor(() => {
      expect(screen.getByText("Job 12345 / Type cpu")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Show plot error details" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy error detail" })).toBeInTheDocument();
  });
});
