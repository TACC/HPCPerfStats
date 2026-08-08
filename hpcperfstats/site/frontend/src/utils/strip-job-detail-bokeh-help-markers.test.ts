import { describe, expect, it } from "vitest";
import {
  jsonItemHasHelpQuestionLabel,
  jsonItemHelpHitRendererIds,
  stripJobDetailBokehHelpMarkers,
} from "./strip-job-detail-bokeh-help-markers";
import { prepareBokehJsonItemForEmbed } from "./remap-bokeh-json-item-ids";

function helpMarkerFixture() {
  return {
    root_id: "p1000",
    doc: {
      roots: [
        {
          type: "object",
          name: "Figure",
          id: "p1000",
          attributes: {
            renderers: [
              {
                type: "object",
                name: "GlyphRenderer",
                id: "p1100",
                attributes: {
                  glyph: {
                    type: "object",
                    name: "Wedge",
                    id: "p1101",
                  },
                },
              },
              {
                type: "object",
                name: "GlyphRenderer",
                id: "p1200",
                attributes: {
                  glyph: {
                    type: "object",
                    name: "Rect",
                    id: "p1201",
                    attributes: {
                      width: 32,
                      height: 28,
                      width_units: "screen",
                      height_units: "screen",
                      fill_alpha: 0,
                      line_alpha: 0,
                    },
                  },
                },
              },
            ],
            center: [
              {
                type: "object",
                name: "Label",
                id: "p1300",
                attributes: { text: "?", text_color: "#0d6efd" },
              },
              {
                type: "object",
                name: "Label",
                id: "p1301",
                attributes: { text: "Keep me" },
              },
            ],
            toolbar: {
              type: "object",
              name: "Toolbar",
              id: "p1400",
              attributes: {
                tools: [
                  {
                    type: "object",
                    name: "HoverTool",
                    id: "p1401",
                    attributes: {
                      renderers: [{ id: "p1200" }],
                      tooltips: "<div>Help desc</div>",
                    },
                  },
                  {
                    type: "object",
                    name: "HoverTool",
                    id: "p1402",
                    attributes: {
                      renderers: [{ id: "p1100" }],
                      tooltips: "@label",
                    },
                  },
                ],
              },
            },
          },
        },
      ],
    },
  };
}

describe("stripJobDetailBokehHelpMarkers", () => {
  it("removes ? Label, help hit Rect renderer, and HoverTool that only targets it", () => {
    const out = stripJobDetailBokehHelpMarkers(helpMarkerFixture());
    expect(jsonItemHasHelpQuestionLabel(out)).toBe(false);
    expect(jsonItemHelpHitRendererIds(out)).toEqual([]);
    const figure = (out as { doc: { roots: Array<{ attributes: Record<string, unknown> }> } })
      .doc.roots[0];
    const center = figure.attributes.center as Array<{ id: string; attributes?: { text?: string } }>;
    expect(center.map((c) => c.attributes?.text)).toEqual(["Keep me"]);
    const renderers = figure.attributes.renderers as Array<{ id: string }>;
    expect(renderers.map((r) => r.id)).toEqual(["p1100"]);
    const tools = (figure.attributes.toolbar as { attributes: { tools: Array<{ id: string }> } })
      .attributes.tools;
    expect(tools.map((t) => t.id)).toEqual(["p1402"]);
  });

  it("preserves unrelated Labels and leaves input unmutated", () => {
    const input = helpMarkerFixture();
    stripJobDetailBokehHelpMarkers(input);
    expect(jsonItemHasHelpQuestionLabel(input)).toBe(true);
    expect(jsonItemHelpHitRendererIds(input)).toEqual(["p1200"]);
  });
});

describe("prepareBokehJsonItemForEmbed stripHelpMarkers", () => {
  it("strips help markers when option is set", () => {
    const out = prepareBokehJsonItemForEmbed(helpMarkerFixture(), {
      stripHelpMarkers: true,
    });
    expect(jsonItemHasHelpQuestionLabel(out)).toBe(false);
  });

  it("keeps help markers by default", () => {
    const out = prepareBokehJsonItemForEmbed(helpMarkerFixture());
    expect(jsonItemHasHelpQuestionLabel(out)).toBe(true);
  });
});
