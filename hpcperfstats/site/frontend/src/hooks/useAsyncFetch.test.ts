import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useAsyncFetch } from "./useAsyncFetch";

describe("useAsyncFetch", () => {
  it("sets data when request succeeds", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useAsyncFetch(fetcher, null));

    await act(async () => {
      await result.current.run();
    });

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual({ ok: true });
    expect(result.current.error).toBe(null);
    expect(result.current.loading).toBe(false);
  });

  it("captures error message when request fails", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAsyncFetch(fetcher, null));

    await act(async () => {
      await expect(result.current.run()).rejects.toThrow("boom");
    });

    expect(result.current.data).toBe(null);
    expect(result.current.error).toBe("boom");
    expect(result.current.loading).toBe(false);
  });
});
