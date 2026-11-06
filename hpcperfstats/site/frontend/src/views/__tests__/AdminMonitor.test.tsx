import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AdminMonitor from "../AdminMonitor";
import { useAdminMonitorSectionQuery } from "@/hooks/use-admin-monitor-section";
import { renderWithProviders } from "@test/vitest/test-utils/render-with-providers";
import { axeSeriousViolations } from "@test/vitest/axe-test-utils";

vi.mock("@/hooks/use-admin-monitor-section", () => ({
  useAdminMonitorSectionQuery: vi.fn(),
}));

vi.mock("../../utils/copy-to-clipboard", () => ({
  copyToClipboard: vi.fn().mockResolvedValue(true),
}));

vi.mock("../../utils/download-text-file", () => ({
  downloadTextFile: vi.fn(),
}));

import { copyToClipboard } from "../../utils/copy-to-clipboard";
import { downloadTextFile } from "../../utils/download-text-file";
import { RABBITMQ_HOST_BUCKET_DOWNLOAD_FILENAME } from "../../utils/format-rabbitmq-host-bucket-download";

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
                    : section === "telemetry_health"
                      ? { telemetry_health: fixture.data }
                      : section === "ingest_coverage"
                        ? { ingest_coverage: fixture.data }
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

  it("downloads all RabbitMQ freshness buckets and shows silent hosts without a count line", async () => {
    mockSectionQuery({
      rabbitmq_hosts: {
        data: [
          {
            host: "fresh.example.com",
            last_time: "2024-01-01T00:00:00Z",
            age_bucket: "ok",
          },
          {
            host: "silent.example.com",
            last_time: null,
            age_bucket: "gt_week",
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
      expect(screen.getByText("fresh.example.com")).toBeInTheDocument();
    });
    expect(screen.getByText("silent.example.com")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("> 1 week")).toBeInTheDocument();
    expect(screen.queryByText(/silent host/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Download RabbitMQ host freshness buckets" }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Download RabbitMQ host freshness buckets" }),
    );
    expect(downloadTextFile).toHaveBeenCalledWith(
      RABBITMQ_HOST_BUCKET_DOWNLOAD_FILENAME,
      [
        "10_m_or_less = [fresh.example.com]",
        "10_m_more = []",
        "1_h_more = []",
        "1_d_more = []",
        "1_w_more = [silent.example.com]",
        "",
      ].join("\n"),
    );
  });

  it("hides Download while RabbitMQ host timestamps are initially loading", () => {
    mockSectionQuery({
      rabbitmq_hosts: { data: [], initialLoading: true },
    });
    renderWithProviders(<AdminMonitor />);
    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in RabbitMQ/i,
      }),
    );
    expect(
      screen.queryByRole("button", { name: "Download RabbitMQ host freshness buckets" }),
    ).not.toBeInTheDocument();
  });

  it("has no serious axe violations with RabbitMQ host timestamps expanded", async () => {
    mockSectionQuery({
      rabbitmq_hosts: {
        data: [
          {
            host: "node2.example.com",
            last_time: "2024-01-01T00:00:00Z",
            age_bucket: "ok",
          },
          {
            host: "silent.example.com",
            last_time: null,
            age_bucket: "gt_week",
          },
        ],
      },
    });
    const view = renderWithProviders(<AdminMonitor />);
    fireEvent.click(
      screen.getByRole("button", {
        name: /Most recent host data timestamps in RabbitMQ/i,
      }),
    );
    await waitFor(() => {
      expect(screen.getByText("silent.example.com")).toBeInTheDocument();
    });
    expect(await axeSeriousViolations(view.container)).toEqual([]);
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

  it("renders management RabbitMQ stats labels including node and rates", async () => {
    mockSectionQuery({
      rabbitmq: {
        data: {
          queue: "horizon",
          messages: 10,
          messages_publish_rate: 1.5,
          mem_used: 1048576,
          alarms: "(none)",
          node_name: "rabbit@rabbitmq",
        },
      },
    });

    renderWithProviders(<AdminMonitor />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /RabbitMQ statistics/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("Publish rate (msg/s)")).toBeInTheDocument();
    });
    expect(screen.getByText("Erlang memory used (bytes)")).toBeInTheDocument();
    expect(screen.getByText("Node alarms")).toBeInTheDocument();
    expect(screen.getByText("Node name")).toBeInTheDocument();
  });

  it("loads telemetry health only when the section is expanded", async () => {
    const enabledCallsBefore: boolean[] = [];
    vi.mocked(useAdminMonitorSectionQuery).mockImplementation(
      ({ section, enabled, pickResponse }) => {
        if (section === "telemetry_health") {
          enabledCallsBefore.push(enabled);
        }
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
        const raw = {
          telemetry_health: {
            window_hours: 12,
            timed_out: false,
            all_zero_events: [
              { type: "host_cpu", event: "user", row_count: 9 },
            ],
            missing_core_types: ["host_mem"],
            hosts_sampled_fqdns: ["n001.example.com"],
            monitor_identities: [
              {
                fqdn: "n001.example.com",
                package_version: "3.0",
                capability_slug: null,
                uname: "Linux x86_64",
                schema_types: ["host_cpu"],
              },
            ],
            findings: [
              {
                kind: "all_zero_core_event",
                severity: "high",
                message: "core zero",
                type: "host_cpu",
                event: "user",
                row_count: 9,
              },
              {
                kind: "all_zero_other_event",
                severity: "low",
                message: "idle",
                type: "host_ib",
                event: "port_xmit_data",
                row_count: 3,
              },
              {
                kind: "missing_core_type",
                severity: "high",
                message: "missing",
                type: "host_mem",
              },
            ],
            monitor_handoff_markdown:
              "# Telemetry health handoff\n\n## Actionable findings\n",
            ok_summary: {
              nonzero_type_event_pairs: 3,
              scanned_note: "Scanned pairs.",
              hosts_sampled: 16,
            },
          },
        };
        return {
          data: pickResponse(raw as never),
          error: null,
          initialLoading: false,
          sectionBusy: false,
          loading: false,
          refetch: vi.fn(),
        };
      },
    );

    renderWithProviders(<AdminMonitor />);
    expect(
      screen.getByRole("button", { name: /Telemetry health \(12h\)/i }),
    ).toBeInTheDocument();
    expect(enabledCallsBefore.every((v) => v === false)).toBe(true);

    fireEvent.click(
      screen.getByRole("button", { name: /Telemetry health \(12h\)/i }),
    );

    await waitFor(() => {
      expect(screen.getByText("host_cpu")).toBeInTheDocument();
    });
    expect(screen.getByText("user")).toBeInTheDocument();
    expect(screen.getByText("host_mem")).toBeInTheDocument();
    expect(screen.getByText(/Bounded scan of non-error/i)).toBeInTheDocument();
    expect(screen.getByText(/16 hosts/i)).toBeInTheDocument();
    expect(screen.getAllByText(/n001\.example\.com/).length).toBeGreaterThan(0);
    expect(screen.getByText(/slug pending RPM/i)).toBeInTheDocument();
    expect(screen.queryByText("port_xmit_data")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /Show 1 informational all-zero pair/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /Copy monitor handoff report/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /Telemetry health \(12h\) — 1 actionable all-zero, 1 missing/i,
      }),
    ).toBeInTheDocument();
  });

  it("copies monitor handoff markdown and expands informational zeros", async () => {
    vi.mocked(copyToClipboard).mockClear();
    vi.mocked(copyToClipboard).mockResolvedValue(true);

    mockSectionQuery({
      telemetry_health: {
        data: {
          window_hours: 12,
          timed_out: false,
          all_zero_events: [
            { type: "host_cpu", event: "user", row_count: 9 },
          ],
          missing_core_types: [],
          findings: [
            {
              kind: "all_zero_other_event",
              severity: "low",
              message: "idle",
              type: "host_ib",
              event: "port_xmit_data",
              row_count: 3,
            },
          ],
          monitor_handoff_markdown: "# handoff body",
          ok_summary: { nonzero_type_event_pairs: 1, scanned_note: "ok" },
        },
      },
    });
    renderWithProviders(<AdminMonitor />);
    fireEvent.click(
      screen.getByRole("button", { name: /Telemetry health \(12h\)/i }),
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Copy monitor handoff report/i }),
      ).toBeInTheDocument();
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Copy monitor handoff report/i }),
    );
    await waitFor(() => {
      expect(copyToClipboard).toHaveBeenCalledWith("# handoff body");
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: /Show 1 informational all-zero pair/i,
      }),
    );
    expect(screen.getByText("port_xmit_data")).toBeInTheDocument();
  });

  it("has no serious axe violations with telemetry health expanded", async () => {
    mockSectionQuery({
      telemetry_health: {
        data: {
          window_hours: 12,
          timed_out: false,
          all_zero_events: [
            { type: "host_cpu", event: "user", row_count: 9 },
          ],
          missing_core_types: ["host_mem"],
          monitor_handoff_markdown: "# handoff",
          ok_summary: { nonzero_type_event_pairs: 3, scanned_note: "ok" },
        },
      },
    });
    const view = renderWithProviders(<AdminMonitor />);
    fireEvent.click(
      screen.getByRole("button", { name: /Telemetry health \(12h\)/i }),
    );
    await waitFor(() => {
      expect(screen.getByText("host_cpu")).toBeInTheDocument();
    });
    expect(await axeSeriousViolations(view.container)).toEqual([]);
  });

  describe("ingest vs Job Detail coverage", () => {
    const BASE = {
      window_minutes: 60,
      consumed_fresh_days: 7,
      group_by: "signature",
      group_by_modes: ["signature", "rack", "schema", "slug"],
      sweeper_state: "ok",
      sweep: { finished_at: 1800000000, hosts_done: 2, hosts_total: 2 },
      last_sweep_at: 1800000000,
      hosts_total: 2,
      hosts_grouped: 2,
      incomplete: false,
      errors: [],
      status_counts: { current: 2, ingest_behind: 0 },
      consumer_registry_empty: false,
      groups_total: 1,
      markdown: "## Ingest vs Job Detail coverage\n",
      groups: [
        {
          key: "abc123",
          hosts: 2,
          is_baseline: true,
          baseline_share: 1,
          gap_count: 1,
          slugs: { "slug-a": 2 },
          host_prefixes: { c101: 2 },
          schema_variants: { v1: 2 },
          class_counts: { unused: 1, derived: 1 },
          pairs: [
            { type: "host_opa", event: "rx_bytes", class: "unused", host_fraction: 1 },
            {
              type: "host_cpu",
              event: "user",
              class: "derived",
              host_fraction: 1,
              stages: ["metrics"],
              last_consumed_at: 1800000000,
            },
          ],
          pairs_total: 2,
          host_rows: [
            {
              fqdn: "c101-001.example.com",
              status: "current",
              ingest_backlog_s: 0,
              open_segment_age_s: 14400,
              scanned_at: 1799999000,
              stale: false,
            },
          ],
        },
      ],
    };

    function openCoverage() {
      fireEvent.click(
        screen.getByRole("button", { name: /Ingest vs Job Detail coverage/i }),
      );
    }

    it("fetches only when opened and passes the group-by mode", async () => {
      mockSectionQuery({ ingest_coverage: { data: BASE } });
      renderWithProviders(<AdminMonitor />);
      const coverageCalls = () =>
        vi
          .mocked(useAdminMonitorSectionQuery)
          .mock.calls.filter(([opts]) => opts.section === "ingest_coverage");
      expect(coverageCalls().every(([opts]) => opts.enabled === false)).toBe(true);
      openCoverage();
      await waitFor(() => {
        expect(screen.getByTestId("ingest-coverage-panel")).toBeInTheDocument();
      });
      expect(coverageCalls().at(-1)?.[0].groupBy).toBe("signature");
      fireEvent.click(screen.getByRole("button", { name: "Rack" }));
      await waitFor(() => {
        expect(coverageCalls().at(-1)?.[0].groupBy).toBe("rack");
      });
      expect(screen.getByText(/Last sweep finished/)).toBeInTheDocument();
    });

    it("shows gap pairs by default and toggles class filters", async () => {
      mockSectionQuery({ ingest_coverage: { data: BASE } });
      renderWithProviders(<AdminMonitor />);
      openCoverage();
      fireEvent.click(await screen.findByRole("button", { name: /abc123/ }));
      expect(screen.getByText("rx_bytes")).toBeInTheDocument();
      expect(screen.queryByText("user")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Used by metrics / plots" }));
      expect(screen.getByText("user")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Unused" }));
      expect(screen.queryByText("rx_bytes")).not.toBeInTheDocument();
      expect(screen.getByText("c101-001.example.com")).toBeInTheDocument();
      expect(screen.getByText("4h 0m")).toBeInTheDocument();
    });

    it("copies the markdown summary", async () => {
      mockSectionQuery({ ingest_coverage: { data: BASE } });
      renderWithProviders(<AdminMonitor />);
      openCoverage();
      fireEvent.click(await screen.findByRole("button", { name: "Copy as markdown" }));
      await waitFor(() => {
        expect(copyToClipboard).toHaveBeenCalledWith(BASE.markdown);
      });
    });

    it.each([
      [
        "incomplete scan",
        { incomplete: true, errors: ["Valkey unavailable."] },
        /Scan incomplete; results are not a healthy signal/,
      ],
      [
        "empty registry",
        { consumer_registry_empty: true, groups: [] },
        /update_metrics has not recorded reads yet/,
      ],
      [
        "sweep disabled",
        { sweeper_state: "sweep_disabled", groups: [] },
        /Coverage sweep disabled on this site/,
      ],
      ["sweeping", { sweeper_state: "sweeping" }, /Sweep in progress: 2 \/ 2 hosts/],
    ])("shows the %s banner", async (_name, patch, text) => {
      mockSectionQuery({ ingest_coverage: { data: { ...BASE, ...patch } } });
      renderWithProviders(<AdminMonitor />);
      openCoverage();
      expect(await screen.findByText(text)).toBeInTheDocument();
    });

    it("greys stale host rows when the sweeper is not running", async () => {
      const staleGroup = {
        ...BASE.groups[0],
        host_rows: [{ ...BASE.groups[0].host_rows[0], stale: true }],
      };
      mockSectionQuery({
        ingest_coverage: {
          data: { ...BASE, sweeper_state: "sweeper_stale", groups: [staleGroup] },
        },
      });
      renderWithProviders(<AdminMonitor />);
      openCoverage();
      expect(
        await screen.findByText(/Coverage sweeper not running/),
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /abc123/ }));
      const row = screen.getByText("c101-001.example.com").closest("tr");
      expect(row).toHaveAttribute("data-stale", "true");
      expect(row).toHaveTextContent("(stale)");
      expect(row).toHaveTextContent("2027-01-15 07:43 UTC");
    });

    it("has no serious axe violations with coverage expanded", async () => {
      mockSectionQuery({ ingest_coverage: { data: BASE } });
      const view = renderWithProviders(<AdminMonitor />);
      openCoverage();
      fireEvent.click(await screen.findByRole("button", { name: /abc123/ }));
      expect(await axeSeriousViolations(view.container)).toEqual([]);
    });
  });
});
