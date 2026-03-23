import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import AdminMonitor from "./AdminMonitor";
import * as apiModule from "../api";

describe("AdminMonitor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders heading", () => {
    render(<AdminMonitor />);
    expect(
      screen.getByText("HPCPerfStats Monitor")
    ).toBeInTheDocument();
  });

  it("loads host stats when section is expanded", async () => {
    vi.spyOn(apiModule.api, "getAdminMonitorSection").mockImplementation(
      async (section) => {
        if (section === "hosts") {
          return {
            host_stats: [
              {
                host: "node1.example.com",
                last_time: "2024-01-01T00:00:00Z",
                age_bucket: "ok",
              },
            ],
          };
        }
        return {};
      }
    );

    render(<AdminMonitor />);

    const button = screen.getByRole("button", {
      name: /Most recent host data timestamps in database/i,
    });
    fireEvent.click(button);

    await waitFor(() => {
      expect(
        screen.getByText("node1.example.com")
      ).toBeInTheDocument();
    });
  });

  it("loads rabbitmq host stats when section is expanded", async () => {
    vi.spyOn(apiModule.api, "getAdminMonitorSection").mockImplementation(
      async (section) => {
        if (section === "rabbitmq_hosts") {
          return {
            rabbitmq_host_stats: [
              {
                host: "node2.example.com",
                last_time: "2024-01-01T00:00:00Z",
                age_bucket: "ok",
              },
            ],
          };
        }
        return {};
      }
    );

    render(<AdminMonitor />);

    const button = screen.getByRole("button", {
      name: /Most recent host data timestamps in RabbitMQ/i,
    });
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText("node2.example.com")).toBeInTheDocument();
    });
  });

  it("refresh button refetches section data with refresh option", async () => {
    const getSectionSpy = vi
      .spyOn(apiModule.api, "getAdminMonitorSection")
      .mockResolvedValue({
        host_stats: [
          {
            host: "node3.example.com",
            last_time: "2024-01-01T00:00:00Z",
            age_bucket: "ok",
          },
        ],
      });

    render(<AdminMonitor />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in database/i,
      })
    );

    await waitFor(() => {
      expect(screen.getByText("node3.example.com")).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByRole("button", { name: "Refresh Data" })[0]);

    await waitFor(() => {
      expect(getSectionSpy).toHaveBeenCalledWith("hosts", { refresh: true });
    });
  });
});

