import { describe, expect, it } from "vitest";
import { parseApiResponse } from "@/api/parse-api-response";
import { resolveResponseSchema } from "@/api/response-schema-registry";

describe("parse-api-response", () => {
  it("resolves admin monitor schema without ReferenceError", () => {
    expect(resolveResponseSchema("GET", "/api/admin_monitor/")).not.toBeNull();
    const parsed = parseApiResponse("GET", "/api/admin_monitor/", {
      section: "hosts",
      data: { host_stats: [] },
    });
    expect(parsed).toEqual({ section: "hosts", data: { host_stats: [] } });
  });

  it("validates pub cluster dashboard bundle", () => {
    expect(() =>
      parseApiResponse("GET", "/api/pub/cluster-dashboard/", {
        machine_name: 123,
      }),
    ).toThrow("API response validation failed");
  });

  it("passes through unmapped success payloads", () => {
    const payload = { custom: true };
    expect(parseApiResponse("GET", "/api/unknown/", payload)).toBe(payload);
  });
});
