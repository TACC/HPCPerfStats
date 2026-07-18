import { z } from "zod";
import { normalizeApiPath, resolveResponseSchema } from "./response-schema-registry";
import { restoreStrippedBokehFields } from "./restore-stripped-bokeh-fields";
import { isDevEnvironment } from "@/utils/is-dev-environment";

export function parseApiResponse<T>(
  method: string,
  url: string,
  payload: unknown,
): T {
  const schema = resolveResponseSchema(method, url);
  if (!schema) return payload as T;
  const parsed = schema.safeParse(payload);
    if (!parsed.success) {
    const routeLabel = `${method.toUpperCase()} ${normalizeApiPath(url)}`;
    const firstIssue = parsed.error.issues[0];
    const detail = firstIssue
      ? ` (${firstIssue.path.join(".")}: ${firstIssue.message})`
      : "";
    if (isDevEnvironment()) {
      console.error(`API response validation failed: ${routeLabel}`, z.treeifyError(parsed.error));
    }
    throw new Error(`API response validation failed: ${routeLabel}${detail}`);
  }
  return restoreStrippedBokehFields(payload, parsed.data) as T;
}
