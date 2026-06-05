import { describe, expect, it } from "vitest";
import {
  tableSortAriaSort,
  tableSortButtonAriaLabel,
  tableSortColumnArrow,
} from "./table-sort-a11y";

describe("tableSortAriaSort", () => {
  it("returns undefined when column is not the active sort column", () => {
    expect(tableSortAriaSort("a", "b", "asc")).toBeUndefined();
  });

  it("returns ascending or descending when column matches", () => {
    expect(tableSortAriaSort("host", "host", "asc")).toBe("ascending");
    expect(tableSortAriaSort("host", "host", "desc")).toBe("descending");
  });
});

describe("tableSortColumnArrow", () => {
  it("returns empty string when column is inactive", () => {
    expect(tableSortColumnArrow("a", "b", "asc")).toBe("");
    expect(tableSortColumnArrow("a", "b", "asc", { leadingSpace: false })).toBe("");
  });

  it("defaults to leading space before arrow", () => {
    expect(tableSortColumnArrow("c", "c", "asc")).toBe(" ▲");
    expect(tableSortColumnArrow("c", "c", "desc")).toBe(" ▼");
  });

  it("omits leading space when leadingSpace is false", () => {
    expect(tableSortColumnArrow("c", "c", "asc", { leadingSpace: false })).toBe("▲");
    expect(tableSortColumnArrow("c", "c", "desc", { leadingSpace: false })).toBe("▼");
  });
});

describe("tableSortButtonAriaLabel", () => {
  it("includes sort direction when column is active", () => {
    expect(tableSortButtonAriaLabel("Host", "host", "host", "asc")).toBe(
      "Sort by Host, ascending",
    );
    expect(tableSortButtonAriaLabel("Host", "host", "runtime", "desc")).toBe("Sort by Host");
  });
});
