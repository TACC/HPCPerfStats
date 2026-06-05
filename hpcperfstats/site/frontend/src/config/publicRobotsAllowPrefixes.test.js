import { describe, expect, it } from "vitest";
import { PUBLIC_ROBOTS_ALLOW_PREFIXES } from "./publicRobotsAllowPrefixes";
import { formatRobotsTxtBody } from "../utils/robots-txt-format";

describe("PUBLIC_ROBOTS_ALLOW_PREFIXES", () => {
  it("is frozen and lists crawlable pub routes", () => {
    expect(Object.isFrozen(PUBLIC_ROBOTS_ALLOW_PREFIXES)).toBe(true);
    expect(PUBLIC_ROBOTS_ALLOW_PREFIXES).toContain("/pub/");
    expect(PUBLIC_ROBOTS_ALLOW_PREFIXES).toContain("/pub/cluster-dashboard");
  });

  it("formats robots.txt with Allow lines in registry order", () => {
    const body = formatRobotsTxtBody(PUBLIC_ROBOTS_ALLOW_PREFIXES);
    const allowLines = body
      .split("\n")
      .filter((line) => line.startsWith("Allow: "))
      .map((line) => line.slice("Allow: ".length));
    expect(allowLines).toEqual([...PUBLIC_ROBOTS_ALLOW_PREFIXES]);
  });
});
