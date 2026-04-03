import { render } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";

/**
 * Smoke test: axe-core runs in Vitest/jsdom on a minimal landmark structure.
 * Full-page scans should be run in a real browser (see TESTING.md).
 */
describe("accessibility smoke (axe-core)", () => {
  it("reports no violations for a minimal accessible fragment", async () => {
    const { container } = render(
      <div>
        <a href="#main-frag">Skip</a>
        <main id="main-frag">
          <h1>Test page</h1>
          <button type="button">Action</button>
        </main>
      </div>,
    );
    const results = await axe.run(container, {
      rules: {
        // jsdom has incomplete CSS / layout; avoid false positives from color-contrast here.
        "color-contrast": { enabled: false },
      },
    });
    expect(results.violations).toEqual([]);
  });
});
