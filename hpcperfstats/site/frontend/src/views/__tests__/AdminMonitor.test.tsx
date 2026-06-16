import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AdminMonitor from "../AdminMonitor";
import { useAdminMonitorSectionQuery } from "@/hooks/use-admin-monitor-section";
import { renderWithProviders } from "@/test-utils/render-with-providers";

vi.mock("@/hooks/use-admin-monitor-section", () => ({
  useAdminMonitorSectionQuery: vi.fn(),
}));

function mockSectionQuery(
  sectionResponses: Record<
    string,
    {
      data?: unknown;
      error?: string | null;
      loading?: boolean;
      initialLoading?: boolean;
      sectionBusy?: boolean;
    }
  >,
) {
  vi.mocked(useAdminMonitorSectionQuery).mockImplementation(
    ({ section, enabled, pickResponse, refreshSeq }) => {
      if (!enabled) {
        return {
          data: null,
          error: null,
          initialLoading: false,
          sectionBusy: false,
          loading: false,
          refetch: vi.fn(),
        };
      }
      const fixture = sectionResponses[section];
      if (!fixture) {
        return {
          data: null,
          error: null,
          initialLoading: false,
          sectionBusy: false,
          loading: false,
          refetch: vi.fn(),
        };
      }
      const raw =
        section === "hosts"
          ? { host_stats: fixture.data }
          : section === "rabbitmq_hosts"
            ? { rabbitmq_host_stats: fixture.data }
            : section === "cache"
              ? { cache_stats: fixture.data }
              : section === "rabbitmq"
                ? { rabbitmq_stats: fixture.data }
                : section === "timescaledb"
                  ? { timescaledb_stats: fixture.data }
                  : section === "xalt"
                    ? { xalt_stats: fixture.data }
                    : {};
      return {
        data: pickResponse(raw as never),
        error: fixture.error ?? null,
        initialLoading:
          fixture.initialLoading ?? (fixture.loading === true && fixture.data == null),
        sectionBusy: fixture.sectionBusy ?? false,
        loading: fixture.loading ?? false,
        refetch: vi.fn(),
        refreshSeq,
      };
    },
  );
}

describe("AdminMonitor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.mocked(useAdminMonitorSectionQuery).mockReset();
  });

  it("renders heading", () => {
    mockSectionQuery({});
    renderWithProviders(<AdminMonitor />);
    expect(screen.getByText("HPCPerfStats Monitor")).toBeInTheDocument();
  });

  it("loads host stats when section is expanded", async () => {
    mockSectionQuery({
      hosts: {
        data: [
          {
            host: "node1.example.com",
            last_time: "2024-01-01T00:00:00Z",
            age_bucket: "ok",
          },
        ],
      },
    });

    renderWithProviders(<AdminMonitor />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in database/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("node1.example.com")).toBeInTheDocument();
    });

    const hostHeader = screen.getByRole("columnheader", { name: /Host\b/i });
    expect(hostHeader).toHaveAttribute("aria-sort", "ascending");
  });

  it("loads rabbitmq host stats when section is expanded", async () => {
    mockSectionQuery({
      rabbitmq_hosts: {
        data: [
          {
            host: "node2.example.com",
            last_time: "2024-01-01T00:00:00Z",
            age_bucket: "ok",
          },
        ],
      },
    });

    renderWithProviders(<AdminMonitor />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in RabbitMQ/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("node2.example.com")).toBeInTheDocument();
    });
  });

  it("paginates host rows when many FQDN hosts are returned", async () => {
    const hosts = Array.from({ length: 155 }, (_, index) => ({
      host: `node${String(index).padStart(3, "0")}.example.com`,
      last_time: "2024-01-01T00:00:00Z",
      age_bucket: "ok",
    }));
    mockSectionQuery({
      hosts: { data: hosts },
    });

    renderWithProviders(<AdminMonitor />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in database/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("node000.example.com")).toBeInTheDocument();
    });
    expect(screen.queryByText("node154.example.com")).not.toBeInTheDocument();
    expect(screen.getByText(/of 155 hosts/)).toBeInTheDocument();
  });

  it("refresh button increments refreshSeq for the hosts section", async () => {
    mockSectionQuery({
      hosts: {
        data: [
          {
            host: "node3.example.com",
            last_time: "2024-01-01T00:00:00Z",
            age_bucket: "ok",
          },
        ],
      },
    });

    renderWithProviders(<AdminMonitor />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in database/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("node3.example.com")).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByRole("button", { name: "Refresh Data" })[0]);

    await waitFor(() => {
      expect(useAdminMonitorSectionQuery).toHaveBeenCalledWith(
        expect.objectContaining({ section: "hosts", refreshSeq: 1, enabled: true }),
      );
    });
  });

  it("omits Refresh Data control while host section is initially loading", async () => {
    mockSectionQuery({
      hosts: {
        data: undefined,
        initialLoading: true,
        loading: true,
      },
    });

    renderWithProviders(<AdminMonitor />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in database/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText(/Loading host timestamps/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /Refresh Data/i })).not.toBeInTheDocument();
  });
});
