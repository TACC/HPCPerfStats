import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useAdminMonitorSectionQuery } from "./use-admin-monitor-section";

vi.mock("@/api/generated/admin/admin", () => ({
  useAdminMonitorRetrieve: vi.fn(),
}));

import { useAdminMonitorRetrieve } from "@/api/generated/admin/admin";

describe("useAdminMonitorSectionQuery", () => {
  it("loads section, picks response, reports loading while fetching", () => {
    vi.mocked(useAdminMonitorRetrieve).mockReturnValue({
      data: { host_stats: [{ host: "node1.example.com" }] },
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useAdminMonitorRetrieve>);

    const { result } = renderHook(() =>
      useAdminMonitorSectionQuery({
        section: "hosts",
        enabled: true,
        pickResponse: (res) => res.host_stats,
      }),
    );

    expect(useAdminMonitorRetrieve).toHaveBeenCalledWith(
      { section: "hosts", refresh: undefined },
      expect.objectContaining({
        query: expect.objectContaining({
          enabled: true,
          queryKey: ["adminMonitor", "hosts", 0],
        }),
      }),
    );
    expect(result.current.data).toEqual([{ host: "node1.example.com" }]);
    expect(result.current.error).toBe(null);
    expect(result.current.loading).toBe(false);
  });

  it("surfaces initialLoading before first payload", () => {
    vi.mocked(useAdminMonitorRetrieve).mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isFetching: true,
      refetch: vi.fn(),
    } as ReturnType<typeof useAdminMonitorRetrieve>);

    const { result } = renderHook(() =>
      useAdminMonitorSectionQuery({
        section: "hosts",
        enabled: true,
        pickResponse: (res) => res.host_stats,
      }),
    );

    expect(result.current.initialLoading).toBe(true);
    expect(result.current.sectionBusy).toBe(false);
  });

  it("surfaces sectionBusy during refetch with prior data", () => {
    vi.mocked(useAdminMonitorRetrieve).mockReturnValue({
      data: { host_stats: [{ host: "node1.example.com" }] },
      error: null,
      isLoading: false,
      isFetching: true,
      refetch: vi.fn(),
    } as ReturnType<typeof useAdminMonitorRetrieve>);

    const { result } = renderHook(() =>
      useAdminMonitorSectionQuery({
        section: "hosts",
        enabled: true,
        pickResponse: (res) => res.host_stats,
      }),
    );

    expect(result.current.initialLoading).toBe(false);
    expect(result.current.sectionBusy).toBe(true);
  });

  it("passes refresh flag when refreshSeq is positive", () => {
    vi.mocked(useAdminMonitorRetrieve).mockReturnValue({
      data: { cache_stats: { keys: 1 } },
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useAdminMonitorRetrieve>);

    renderHook(() =>
      useAdminMonitorSectionQuery({
        section: "cache",
        enabled: true,
        refreshSeq: 2,
        pickResponse: (res) => res.cache_stats ?? null,
      }),
    );

    expect(useAdminMonitorRetrieve).toHaveBeenCalledWith(
      { section: "cache", refresh: "1" },
      expect.objectContaining({
        query: expect.objectContaining({
          queryKey: ["adminMonitor", "cache", 2],
        }),
      }),
    );
  });

  it("records error message on failure", () => {
    vi.mocked(useAdminMonitorRetrieve).mockReturnValue({
      data: undefined,
      error: new Error("net"),
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useAdminMonitorRetrieve>);

    const { result } = renderHook(() =>
      useAdminMonitorSectionQuery({
        section: "cache",
        enabled: true,
        pickResponse: () => null,
      }),
    );

    expect(result.current.data).toBe(null);
    expect(result.current.error).toBe("net");
    expect(result.current.loading).toBe(false);
  });

  it("does not fetch when disabled", () => {
    vi.mocked(useAdminMonitorRetrieve).mockReturnValue({
      data: undefined,
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as ReturnType<typeof useAdminMonitorRetrieve>);

    const { result } = renderHook(() =>
      useAdminMonitorSectionQuery({
        section: "hosts",
        enabled: false,
        pickResponse: (res) => res.host_stats,
      }),
    );

    expect(useAdminMonitorRetrieve).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        query: expect.objectContaining({ enabled: false }),
      }),
    );
    expect(result.current.data).toBe(null);
    expect(result.current.loading).toBe(false);
  });
});
