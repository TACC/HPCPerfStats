import { describe, expect, it } from "vitest";
import { isSafeHttpUrl } from "./safe-external-url";

describe("isSafeHttpUrl", () => {
  it("accepts http and https URLs", () => {
    expect(isSafeHttpUrl("https://example.com/path")).toBe(true);
    expect(isSafeHttpUrl("http://localhost:8000/x")).toBe(true);
  });

  it("rejects javascript, data, and protocol-relative URLs", () => {
    expect(isSafeHttpUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeHttpUrl("data:text/html,evil")).toBe(false);
    expect(isSafeHttpUrl("//evil.example/phish")).toBe(false);
  });

  it("rejects empty and non-string values", () => {
    expect(isSafeHttpUrl("")).toBe(false);
    expect(isSafeHttpUrl("   ")).toBe(false);
    expect(isSafeHttpUrl(null)).toBe(false);
    expect(isSafeHttpUrl(undefined)).toBe(false);
  });
});
