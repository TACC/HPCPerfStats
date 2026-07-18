import { describe, expect, it } from "vitest";
import {
  filterIdentitySearchParamsKey,
  stripPresentationParams,
} from "./filter-identity-params";

describe("filter-identity-params", () => {
  it("stripPresentationParams drops order_by page view tab", () => {
    expect(
      stripPresentationParams({
        end_time__date: "2024-01-15",
        queue: "normal",
        page: "2",
        order_by: "-runtime",
        view: "charts",
        tab: "summary",
        performance_sort_rank: "1",
      }),
    ).toEqual({
      end_time__date: "2024-01-15",
      queue: "normal",
    });
  });

  it("filterIdentitySearchParamsKey ignores presentation keys and sorts", () => {
    const sp = new URLSearchParams(
      "order_by=-end_time&queue=normal&page=3&end_time__date=2024-01-15",
    );
    expect(filterIdentitySearchParamsKey(sp)).toBe(
      "end_time__date=2024-01-15&queue=normal",
    );
  });

  it("filterIdentitySearchParamsKey stays stable when only order_by changes", () => {
    const a = filterIdentitySearchParamsKey(
      new URLSearchParams("end_time__date=2024-01-15&order_by=-end_time"),
    );
    const b = filterIdentitySearchParamsKey(
      new URLSearchParams("end_time__date=2024-01-15&order_by=username"),
    );
    expect(a).toBe(b);
  });
});
