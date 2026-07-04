import { describe, expect, it } from "vitest";
import { parseApiErrorBody } from "./api-error";

describe("parseApiErrorBody", () => {
  it("does not cast unvalidated object payloads into error body fields", () => {
    const err = parseApiErrorBody({ unexpected: "value", nested: { x: 1 } }, 500);
    expect(err.body).toEqual({});
    expect(err.message).toBe("HTTP 500");
  });

  it("uses string payload as detail when schema validation fails", () => {
    const err = parseApiErrorBody("Server overloaded", 503);
    expect(err.body).toEqual({ detail: "Server overloaded" });
    expect(err.message).toBe("Server overloaded");
  });

  it("accepts validated error body shape", () => {
    const err = parseApiErrorBody({ detail: "Forbidden" }, 403);
    expect(err.body.detail).toBe("Forbidden");
    expect(err.message).toBe("Forbidden");
  });
});
