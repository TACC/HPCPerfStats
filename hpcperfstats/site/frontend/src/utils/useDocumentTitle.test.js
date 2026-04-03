import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useDocumentTitle } from "./useDocumentTitle";

describe("useDocumentTitle", () => {
  afterEach(() => {
    document.title = "";
  });

  it("sets title with suffix when title is non-empty", () => {
    renderHook(() => useDocumentTitle("Search home"));
    expect(document.title).toBe("Search home | HPCPerfStats");
  });

  it("uses only suffix when title is empty", () => {
    renderHook(() => useDocumentTitle(""));
    expect(document.title).toBe("HPCPerfStats");
  });

  it("respects custom suffix", () => {
    renderHook(() => useDocumentTitle("Page", { suffix: "Custom" }));
    expect(document.title).toBe("Page | Custom");
  });
});
