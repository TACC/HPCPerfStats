import { describe, expect, it } from "vitest";
import { resolveJobListSelectionContext } from "./job-list-selection-context";

describe("resolveJobListSelectionContext", () => {
  it("merges browse date from query when path segments are normalized away", () => {
    const ctx = resolveJobListSelectionContext(
      new URLSearchParams("end_time__date=2024-01-15&queue=normal"),
      {},
    );
    expect(ctx.date).toBe("2024-01-15");
    expect(ctx.endTimeDate).toBe("2024-01-15");
    expect(ctx.queue).toBe("normal");
  });

  it("prefers path year over query when both present", () => {
    const ctx = resolveJobListSelectionContext(
      new URLSearchParams("end_time__date=2024"),
      { year: "2023" },
    );
    expect(ctx.year).toBe("2023");
  });

  it("reads order_by from query", () => {
    const ctx = resolveJobListSelectionContext(
      new URLSearchParams("order_by=username"),
      {},
    );
    expect(ctx.orderBy).toBe("username");
  });
});
