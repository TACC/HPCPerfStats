import { describe, expect, it } from "vitest";

import { PUBLIC_ROBOTS_ALLOW_PREFIXES } from "../config/publicRobotsAllowPrefixes";
import { formatRobotsTxtBody } from "../utils/robots-txt-format";

describe("robots.txt registry", () => {
  it("formats body with User-agent, Allow lines, and blanket Disallow", () => {
    const body = formatRobotsTxtBody(PUBLIC_ROBOTS_ALLOW_PREFIXES);
    expect(body.startsWith("User-agent: *")).toBe(true);
    for (const prefix of PUBLIC_ROBOTS_ALLOW_PREFIXES) {
      expect(body).toContain(`Allow: ${prefix}`);
    }
    expect(body).toContain("Disallow: /");
    const lines = body.split("\n");
    expect(lines[lines.length - 1]).toBe("Disallow: /");
  });
});
