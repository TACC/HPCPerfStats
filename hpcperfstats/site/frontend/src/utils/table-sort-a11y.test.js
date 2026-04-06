import { describe, expect, it } from "vitest";
import { tableSortAriaSort, tableSortColumnArrow } from "./table-sort-a11y";

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
