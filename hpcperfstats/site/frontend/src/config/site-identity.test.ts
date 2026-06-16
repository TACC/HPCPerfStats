import { describe, expect, it } from "vitest";
import { SITE_MACHINE_NAME, SITE_MACHINE_SHORT_NAME } from "./site-identity";

describe("site-identity", () => {
  it("exports build-time machine identity strings", () => {
    expect(typeof SITE_MACHINE_NAME).toBe("string");
    expect(typeof SITE_MACHINE_SHORT_NAME).toBe("string");
  });
});
