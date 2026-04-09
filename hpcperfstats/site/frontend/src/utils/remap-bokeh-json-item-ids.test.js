import { describe, expect, it } from "vitest";
import { prepareBokehJsonItemForEmbed } from "./remap-bokeh-json-item-ids";

describe("prepareBokehJsonItemForEmbed", () => {
  it("returns a deep clone with remapped pNNN ids consistently", () => {
    const item = {
      doc: {
        roots: [
          {
            type: "object",
            name: "Figure",
            id: "p1006",
            attributes: {
              x_range: { type: "object", name: "DataRange1d", id: "p1007" },
            },
          },
        ],
      },
      root_id: "p1006",
      root_ids: ["p1006"],
    };
    const out = prepareBokehJsonItemForEmbed(item);
    expect(out).not.toBe(item);
    expect(out.root_id).toMatch(/^p\d+$/);
    expect(out.root_id).not.toBe("p1006");
    expect(out.root_ids[0]).toBe(out.root_id);
    expect(out.doc.roots[0].id).toBe(out.root_id);
    expect(out.doc.roots[0].attributes.x_range.id).toMatch(/^p\d+$/);
    expect(out.doc.roots[0].attributes.x_range.id).not.toBe("p1007");
  });

  it("leaves non-Bokeh ids unchanged", () => {
    const item = {
      doc: { roots: [{ id: "custom-root", type: "object" }] },
      root_id: "custom-root",
    };
    const out = prepareBokehJsonItemForEmbed(item);
    expect(out.root_id).toBe("custom-root");
    expect(out.doc.roots[0].id).toBe("custom-root");
  });

  it("assigns disjoint id blocks for successive prepares (worker id collision avoidance)", () => {
    const mk = (pid) => ({
      doc: { roots: [{ type: "object", name: "Figure", id: pid }] },
      root_id: pid,
    });
    const a = prepareBokehJsonItemForEmbed(mk("p1006"));
    const b = prepareBokehJsonItemForEmbed(mk("p1006"));
    expect(a.root_id).not.toBe(b.root_id);
    expect(a.root_id).toMatch(/^p\d+$/);
    expect(b.root_id).toMatch(/^p\d+$/);
  });
});
