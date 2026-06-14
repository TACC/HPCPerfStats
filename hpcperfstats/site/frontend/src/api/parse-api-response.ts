/**
 * Runtime validation for Orval-generated API responses at the customFetch boundary.
 */
import { normalizeApiPath, resolveResponseSchema } from "./response-schema-registry";
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
    if (isDevEnvironment()) {
      // eslint-disable-next-line no-console
      console.error(`API response validation failed: ${routeLabel}`, parsed.error.flatten());
    }
    throw new Error(`API response validation failed: ${routeLabel}`);
  }
  return parsed.data as T;
}
