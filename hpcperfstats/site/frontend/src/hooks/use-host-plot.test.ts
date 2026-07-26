import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useHostPlotQuery } from "./use-host-plot";

vi.mock("@/api/generated/hosts/hosts", () => ({
  useHostPlotRetrieve: vi.fn(),
}));

import { useHostPlotRetrieve } from "@/api/generated/hosts/hosts";

describe("useHostPlotQuery", () => {
  beforeEach(() => {
    vi.mocked(useHostPlotRetrieve).mockReset();
  });

  it("fetches when host and range are provided", () => {
    vi.mocked(useHostPlotRetrieve).mockReturnValue({
      data: { host: "node1", plot_item: null },
      error: null,
      isLoading: false,
    } as ReturnType<typeof useHostPlotRetrieve>);

    const params = {
      host: "node1.example.com",
      end_time__gte: "2024-01-01T00:00:00Z",
      end_time__lte: "now()",
    };
    const { result } = renderHook(() => useHostPlotQuery(params));

    expect(useHostPlotRetrieve).toHaveBeenCalledWith(
      params,
      expect.objectContaining({
        query: expect.objectContaining({
          enabled: true,
          select: expect.any(Function),
          placeholderData: expect.any(Function),
        }),
      }),
    );
    expect(result.current.data?.host).toBe("node1");
    expect(result.current.loading).toBe(false);
  });

  it("stays disabled without host", () => {
    vi.mocked(useHostPlotRetrieve).mockReturnValue({
      data: undefined,
      error: null,
      isLoading: false,
    } as ReturnType<typeof useHostPlotRetrieve>);

    const { result } = renderHook(() => useHostPlotQuery(null));

    expect(useHostPlotRetrieve).toHaveBeenCalledWith(
      { host: "", end_time__gte: "" },
      expect.objectContaining({
        query: expect.objectContaining({
          enabled: false,
          select: expect.any(Function),
          placeholderData: expect.any(Function),
        }),
      }),
    );
    expect(result.current.data).toBe(null);
    expect(result.current.loading).toBe(false);
  });
});
