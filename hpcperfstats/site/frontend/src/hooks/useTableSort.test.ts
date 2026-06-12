import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useTableSort } from "./useTableSort";

describe("useTableSort", () => {
  it("toggles direction on same column and resets on new column", () => {
    const { result } = renderHook(() => useTableSort("failed_rate", "desc", "desc"));

    expect(result.current.sort).toEqual({ column: "failed_rate", direction: "desc" });

    act(() => {
      result.current.onSort("failed_rate");
    });
    expect(result.current.sort).toEqual({ column: "failed_rate", direction: "asc" });

    act(() => {
      result.current.onSort("username");
    });
    expect(result.current.sort).toEqual({ column: "username", direction: "desc" });
  });
});
