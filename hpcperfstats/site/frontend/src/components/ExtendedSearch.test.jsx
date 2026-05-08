import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExtendedSearch from "./ExtendedSearch";
import * as useHomeOptionsModule from "../hooks/use-home-options";
import { EXTENDED_SEARCH_PARAMETER_DEFINITIONS } from "../utils/extended-search-parameters";

vi.mock("../hooks/use-home-options", () => ({
  useHomeOptions: vi.fn(),
}));

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderExtendedSearch(ui, { initialEntries = ["/jobs"] } = {}) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route
          path="/jobs"
          element={
            <>
              {ui}
              <LocationProbe />
            </>
          }
        />
        <Route
          path="/job/:jid"
          element={
            <>
              <div data-testid="job-page">job</div>
              <LocationProbe />
            </>
          }
        />
        <Route path="/host/:host/plot" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function setHomeOptions(options) {
  useHomeOptionsModule.useHomeOptions.mockReturnValue({
    options,
    error: null,
    loading: false,
  });
}

function regexpEscape(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function submitField(label, value) {
  const user = userEvent.setup();
  const field = screen.getByLabelText(`${label}?`);
  if (field.tagName === "SELECT") {
    await user.selectOptions(field, value);
  } else {
    await user.clear(field);
    await user.type(field, value);
  }
  await user.click(screen.getByRole("button", { name: /^search$/i }));
}

function parsedLocation() {
  const raw = screen.getByTestId("location").textContent;
  return new URL(raw, "http://hpcperfstats.test");
}

describe("ExtendedSearch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    setHomeOptions({
      metrics: [{ metric: "avg_freq", units: "GHz" }],
      queues: ["normal"],
      states: ["COMPLETED"],
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

  it("renders help controls for every static parameter and derived metric", () => {
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);
    for (const param of EXTENDED_SEARCH_PARAMETER_DEFINITIONS) {
      expect(
        screen.getAllByRole("button", {
          name: new RegExp(`help: ${regexpEscape(param.metadataKey)}`, "i"),
        }).length,
      ).toBeGreaterThan(0);
    }
    expect(screen.getByRole("button", { name: /help: avg_freq/i })).toBeInTheDocument();
  });

  it.each([
    ["host", "n001.cluster.example"],
    ["username", "alice"],
    ["account__icontains", "project-a"],
    ["state", "COMPLETED"],
    ["queue", "normal"],
    ["end_time__gte", "2024-01-01"],
    ["end_time__lte", "2024-01-31"],
    ["runtime__gte", "60"],
    ["runtime__lte", "3600"],
    ["nhosts__gte", "2"],
    ["nhosts__lte", "8"],
    ["node_hrs__gte", "4"],
    ["node_hrs__lte", "24"],
  ])("submits %s to the job list query", async (paramName, value) => {
    const param = EXTENDED_SEARCH_PARAMETER_DEFINITIONS.find((item) => item.name === paramName);
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);

    await submitField(param.label, value);

    const url = parsedLocation();
    expect(url.pathname).toBe("/jobs");
    expect(url.searchParams.get(paramName)).toBe(value);
  });

  it("submits derived metric minimum and maximum filters to the job list query", async () => {
    const user = userEvent.setup();
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(/avg_freq minimum/i), "1.5");
    await user.type(screen.getByLabelText(/avg_freq maximum/i), "3.2");
    await user.click(screen.getByRole("button", { name: /^search$/i }));

    const url = parsedLocation();
    expect(url.pathname).toBe("/jobs");
    expect(url.searchParams.get("metrics_avg_freq__gte")).toBe("1.5");
    expect(url.searchParams.get("metrics_avg_freq__lte")).toBe("3.2");
  });

  it("routes host plus start date to the host plot", async () => {
    const user = userEvent.setup();
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);

    await user.type(screen.getByLabelText("Host?"), "n001.cluster.example");
    await user.type(screen.getByLabelText("Start Date?"), "2024-01-01");
    await user.click(screen.getByRole("button", { name: /^search$/i }));

    const url = parsedLocation();
    expect(url.pathname).toBe("/host/n001.cluster.example/plot");
    expect(url.searchParams.get("end_time__gte")).toBe("2024-01-01");
    expect(url.searchParams.get("end_time__lte")).toBe("now()");
  });
});
