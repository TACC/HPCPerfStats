/**
 * Runtime validation for Orval-generated API responses at the customFetch boundary.
 */
import { resolveResponseSchema } from "./response-schema-registry";
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
    if (isDevEnvironment()) {
      // eslint-disable-next-line no-console
      console.error("API response validation failed", parsed.error.flatten());
    }
    throw new Error("API response validation failed");
  }
  return parsed.data as T;
}
