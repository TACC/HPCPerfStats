import type { ReactElement } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExtendedSearch from "./ExtendedSearch";
import * as useHomeOptionsModule from "../hooks/use-home-options";
import { axeSeriousViolations } from "@test/vitest/axe-test-utils";
import { EXTENDED_SEARCH_PARAMETER_DEFINITIONS } from "../utils/extended-search-parameters";
import { lastRouterPushUrl } from "@test/vitest/test-utils/next-navigation-state";

vi.mock("../hooks/use-home-options", () => ({
  useHomeOptions: vi.fn(),
}));

function renderExtendedSearch(ui: ReactElement) {
  return render(ui);
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

function getFieldByLabel(label) {
  const namePattern = new RegExp(`^${regexpEscape(label)}`, "i");
  const combobox = screen.queryByRole("combobox", { name: namePattern });
  if (combobox) return combobox;
  const textbox = screen.queryByRole("textbox", { name: namePattern });
  if (textbox) return textbox;
  return screen.getByLabelText(namePattern);
}

async function submitField(label, value) {
  const user = userEvent.setup();
  const field = getFieldByLabel(label);
  const role = field.getAttribute("role");
  if (role === "combobox" || field.tagName === "BUTTON") {
    await user.click(field);
    const option = await screen.findByRole("option", { name: value });
    await user.click(option);
  } else if (field.tagName === "SELECT") {
    await user.selectOptions(field, value);
  } else {
    await user.clear(field);
    await user.type(field, value);
  }
  await user.click(screen.getByRole("button", { name: /^search$/i }));
}

function parsedLocation() {
  return lastRouterPushUrl();
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
    await user.type(screen.getByRole("textbox", { name: /job id/i }), "991");
    await user.click(screen.getByRole("button", { name: /^search$/i }));
    expect(lastRouterPushUrl().pathname).toBe("/machine/job/991/");
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
    expect(url.pathname).toBe("/machine/jobs/");
    expect(url.searchParams.get(paramName)).toBe(value);
  });

  it("submits derived metric minimum and maximum filters to the job list query", async () => {
    const user = userEvent.setup();
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(/avg_freq minimum/i), "1.5");
    await user.type(screen.getByLabelText(/avg_freq maximum/i), "3.2");
    await user.click(screen.getByRole("button", { name: /^search$/i }));

    const url = parsedLocation();
    expect(url.pathname).toBe("/machine/jobs/");
    expect(url.searchParams.get("metrics_avg_freq__gte")).toBe("1.5");
    expect(url.searchParams.get("metrics_avg_freq__lte")).toBe("3.2");
  });

  it("has no serious axe violations inside dialog shell", async () => {
    const view = renderExtendedSearch(
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="extended-search-dialog-title"
      >
        <ExtendedSearch onClose={vi.fn()} />
      </div>,
    );
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  it("routes host plus start date to the host plot", async () => {
    const user = userEvent.setup();
    renderExtendedSearch(<ExtendedSearch onClose={vi.fn()} />);

    await user.type(getFieldByLabel("Host"), "n001.cluster.example");
    await user.type(getFieldByLabel("Earliest job end date"), "2024-01-01");
    await user.click(screen.getByRole("button", { name: /^search$/i }));

    const url = parsedLocation();
    expect(url.pathname).toBe("/machine/host/n001.cluster.example/plot/");
    expect(url.searchParams.get("end_time__gte")).toBe("2024-01-01");
    expect(url.searchParams.get("end_time__lte")).toBe("now()");
  });

  it("shows variable help popover above extended search backdrop", async () => {
    renderExtendedSearch(
      <div
        className="fixed inset-0 z-[var(--z-modal-backdrop)] overflow-y-auto bg-black/35"
        data-testid="extended-search-backdrop"
      >
        <div role="dialog" aria-labelledby="extended-search-dialog-title">
          <ExtendedSearch onClose={vi.fn()} />
        </div>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: /help: host/i }));
    const panel = await screen.findByTestId("variable-info-tooltip");
    expect(panel).toHaveAttribute("data-open");
    expect(document.body.contains(panel)).toBe(true);
    expect(panel).toHaveTextContent(/host/i);
  });
});
