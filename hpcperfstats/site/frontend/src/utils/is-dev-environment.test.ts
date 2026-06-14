import { afterEach, describe, expect, it } from "vitest";
import { isDevEnvironment } from "./is-dev-environment";

describe("isDevEnvironment", () => {
  const originalNodeEnv = process.env.NODE_ENV;

  afterEach(() => {
    process.env.NODE_ENV = originalNodeEnv;
  });

  it("returns true when NODE_ENV is development", () => {
    process.env.NODE_ENV = "development";
    expect(isDevEnvironment()).toBe(true);
  });
});
