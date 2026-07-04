import type { BokehJsonItem } from "@/types/bokeh";

export { OPENAPI_BOKEH_JSON_ITEM } from "@test/wire-audit/bokeh-wire-fixtures";

/** Minimal json_item that passes parseBokehJsonItem (see bokeh-json-item-schema.test.ts). */
export const VALID_BOKEH_JSON_ITEM: BokehJsonItem = {
  doc: {
    root_ids: ["p1001"],
    roots: [{ id: "p1001", type: "object", name: "GridPlot" }],
  },
};
