import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useHomeOptions } from "./use-home-options";

vi.mock("@/api/generated/home/home", () => ({
  useHomeRetrieve: vi.fn(),
}));

describe("useHomeOptions", () => {
  it("exposes options after load", async () => {
    const { useHomeRetrieve } = await import("@/api/generated/home/home");
    vi.mocked(useHomeRetrieve).mockReturnValue({
      data: { metrics: [1] },
      error: null,
      isLoading: false,
    } as ReturnType<typeof useHomeRetrieve>);

    const { result } = renderHook(() => useHomeOptions());

    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe(null);
    expect(result.current.options).toEqual({ metrics: [1] });
  });

  it("records error on failure", async () => {
    const { useHomeRetrieve } = await import("@/api/generated/home/home");
    vi.mocked(useHomeRetrieve).mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    } as ReturnType<typeof useHomeRetrieve>);

    const { result } = renderHook(() => useHomeOptions());

    expect(result.current.loading).toBe(false);
    expect(result.current.options).toBe(null);
    expect(result.current.error).toBe("boom");
  });
});
