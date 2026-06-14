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
});
