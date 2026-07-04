import { axe } from "jest-axe";

/** Matches `hpcperfstats.tests.playwright_axe` WCAG tag scope. */
export const AXE_WCAG_RUN_OPTIONS = {
  runOnly: {
    type: "tag",
    values: ["wcag2a", "wcag2aa", "wcag21aa"],
  },
};

/**
 * Returns violations with impact serious or critical only (aligned with Playwright helper).
 */
export async function axeSeriousViolations(container, options = {}) {
  const results = await axe(container, { ...AXE_WCAG_RUN_OPTIONS, ...options });
  return results.violations.filter((v) =>
    ["serious", "critical"].includes(String(v.impact || "").toLowerCase()),
  );
}
