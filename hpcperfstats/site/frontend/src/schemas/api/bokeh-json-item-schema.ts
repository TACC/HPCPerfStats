import { z } from "zod";

/** Minimal Bokeh json_item document shape required before embed_item. */
const bokehRootNodeSchema = z.looseObject({
  id: z.union([z.string(), z.number()]).optional(),
});

export const bokehJsonItemSchema = z
  .looseObject({
    doc: z.looseObject({
      roots: z.array(bokehRootNodeSchema).optional(),
      root_ids: z.array(z.union([z.string(), z.number()])).optional(),
    }),
  })
  .refine(
  (value) => {
    const doc = value.doc as Record<string, unknown> | undefined;
    if (!doc || typeof doc !== "object") return false;
    const rootIds = doc.root_ids;
    if (Array.isArray(rootIds) && rootIds.length > 0) return true;
    const roots = doc.roots;
    if (!Array.isArray(roots) || roots.length === 0) return false;
    return roots.some((node) => {
      if (!node || typeof node !== "object") return false;
      const id = (node as Record<string, unknown>).id;
      if (typeof id === "string") return id.trim().length > 0;
      return typeof id === "number" && Number.isFinite(id);
    });
  },
  { message: "Invalid Bokeh json_item document" },
);

export type BokehJsonItemParsed = z.infer<typeof bokehJsonItemSchema>;

export function parseBokehJsonItem(value: unknown): BokehJsonItemParsed | null {
  const result = bokehJsonItemSchema.safeParse(value);
  return result.success ? result.data : null;
}
