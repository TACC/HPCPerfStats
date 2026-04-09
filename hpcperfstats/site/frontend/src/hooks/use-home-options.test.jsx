import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useHomeOptions } from "./use-home-options";

vi.mock("../api", () => ({
  api: {
    getHomeOptions: vi.fn(),
  },
}));

describe("useHomeOptions", () => {
  it("exposes options after load", async () => {
    const { api } = await import("../api");
    api.getHomeOptions.mockResolvedValue({ metrics: [1] });

    const { result } = renderHook(() => useHomeOptions());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe(null);
    expect(result.current.options).toEqual({ metrics: [1] });
    await new Promise((r) => setTimeout(r, 40));
    expect(api.getHomeOptions.mock.calls.length).toBeLessThan(5);
  });

  it("records error on failure", async () => {
    const { api } = await import("../api");
    api.getHomeOptions.mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useHomeOptions());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options).toBe(null);
    expect(result.current.error).toBe("boom");
    await new Promise((r) => setTimeout(r, 40));
    expect(api.getHomeOptions.mock.calls.length).toBeLessThan(5);
  });
});
