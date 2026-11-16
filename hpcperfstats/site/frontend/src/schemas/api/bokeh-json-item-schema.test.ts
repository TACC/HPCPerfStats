import { describe, expect, it } from "vitest";
import { bokehJsonItemSchema, parseBokehJsonItem } from "./bokeh-json-item-schema";

describe("bokehJsonItemSchema", () => {
  it("accepts minimal valid json_item with root_ids", () => {
    const item = {
      doc: {
        root_ids: ["p1001"],
        roots: [{ id: "p1001", type: "object", name: "GridPlot" }],
      },
    };
    expect(parseBokehJsonItem(item)).toEqual(item);
  });

  it("rejects missing doc", () => {
    expect(parseBokehJsonItem({})).toBeNull();
    expect(bokehJsonItemSchema.safeParse({}).success).toBe(false);
  });

  it("rejects doc without roots or root_ids", () => {
    expect(parseBokehJsonItem({ doc: { roots: [] } })).toBeNull();
  });

  it("accepts roots-only docs when some root has a non-empty id", () => {
    expect(parseBokehJsonItem({ doc: { roots: [{ id: "p1" }] } })).not.toBeNull();
    expect(parseBokehJsonItem({ doc: { roots: [{}, { id: 7 }] } })).not.toBeNull();
    expect(parseBokehJsonItem({ doc: { roots: [{ id: "  p2 " }] } })).not.toBeNull();
  });

  it("rejects roots-only docs whose roots carry no usable id", () => {
    expect(parseBokehJsonItem({ doc: { roots: [{}] } })).toBeNull();
    expect(parseBokehJsonItem({ doc: { roots: [{ id: "   " }] } })).toBeNull();
    expect(parseBokehJsonItem({ doc: { roots: [{ id: Number.POSITIVE_INFINITY }] } })).toBeNull();
  });
});
