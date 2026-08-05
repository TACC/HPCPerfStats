import { describe, expect, it } from "vitest";
import { fingerprintBokehJsonItem } from "./fingerprint-bokeh-json-item";

describe("fingerprintBokehJsonItem", () => {
  it("returns empty string for nullish", () => {
    expect(fingerprintBokehJsonItem(null)).toBe("");
    expect(fingerprintBokehJsonItem(undefined)).toBe("");
  });

  it("matches for deep-equal objects with different refs", () => {
    const a = { root_id: "r", doc: { x: 1 } };
    const b = { root_id: "r", doc: { x: 1 } };
    expect(fingerprintBokehJsonItem(a)).toBe(fingerprintBokehJsonItem(b));
  });

  it("differs when doc content differs", () => {
    expect(fingerprintBokehJsonItem({ doc: { a: 1 } })).not.toBe(
      fingerprintBokehJsonItem({ doc: { a: 2 } }),
    );
  });
});
