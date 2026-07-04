import type { BokehJsonItem } from "@/types/bokeh";

/** Minimal json_item matching OpenAPI BokehJsonItemSerializer (Orval Zod wire validation). */
export const OPENAPI_BOKEH_JSON_ITEM = {
  doc: {
    root_ids: ["p1001"],
    roots: [{ id: "p1001" }],
  },
} as const;

/** Minimal json_item that passes parseBokehJsonItem (see bokeh-json-item-schema.test.ts). */
export const VALID_BOKEH_JSON_ITEM: BokehJsonItem = {
  doc: {
    root_ids: ["p1001"],
    roots: [{ id: "p1001", type: "object", name: "GridPlot" }],
  },
};
