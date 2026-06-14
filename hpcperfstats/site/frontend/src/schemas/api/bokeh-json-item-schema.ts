import { z } from "zod";

/** Minimal Bokeh json_item document shape required before embed_item. */
const bokehRootNodeSchema = z
  .object({
    id: z.union([z.string(), z.number()]).optional(),
  })
  .passthrough();

export const bokehJsonItemSchema = z
  .object({
    doc: z
      .object({
        roots: z.array(bokehRootNodeSchema).optional(),
        root_ids: z.array(z.union([z.string(), z.number()])).optional(),
      })
      .passthrough(),
  })
  .passthrough()
  .refine(
  (value) => {
    const doc = value.doc as Record<string, unknown> | undefined;
    if (!doc || typeof doc !== "object") return false;
    const rootIds = doc.root_ids;
    if (Array.isArray(rootIds) && rootIds.length > 0) return true;
    const roots = doc.roots;
    if (!Array.isArray(roots) || roots.length === 0) return false;
    const rootIdSet = new Set(
      roots
        .map((node) => {
          if (!node || typeof node !== "object") return "";
          const id = (node as Record<string, unknown>).id;
          if (typeof id === "string") return id.trim();
          if (typeof id === "number" && Number.isFinite(id)) return String(id);
          return "";
        })
        .filter((id) => id.length > 0),
    );
    return roots.some((node) => {
      if (!node || typeof node !== "object") return false;
      const id = (node as Record<string, unknown>).id;
      const key =
        typeof id === "string"
          ? id.trim()
          : typeof id === "number" && Number.isFinite(id)
            ? String(id)
            : "";
      return key.length > 0 && rootIdSet.has(key);
    });
  },
  { message: "Invalid Bokeh json_item document" },
);

export type BokehJsonItemParsed = z.infer<typeof bokehJsonItemSchema>;

export function parseBokehJsonItem(value: unknown): BokehJsonItemParsed | null {
  const result = bokehJsonItemSchema.safeParse(value);
  return result.success ? result.data : null;
}
