import { z } from "zod";
import { isDevEnvironment } from "@/utils/is-dev-environment";

export const apiErrorBodySchema = z
  .object({
    error: z.string().optional(),
    detail: z.string().optional(),
    login_url: z.string().optional(),
    message: z.string().optional(),
  })
  .catchall(z.unknown());

export type ApiErrorBody = z.infer<typeof apiErrorBodySchema>;

export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody;

  constructor(message: string, status: number, body: ApiErrorBody) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export function extractApiErrorMessage(
  body: ApiErrorBody,
  status: number,
  fallback?: string,
): string {
  const detail = typeof body.detail === "string" ? body.detail.trim() : "";
  const error = typeof body.error === "string" ? body.error.trim() : "";
  const message = typeof body.message === "string" ? body.message.trim() : "";

  if (detail && (!error || detail.length > error.length || error.includes("_"))) {
    return detail;
  }
  if (error) return error;
  if (message) return message;
  return fallback ?? `HTTP ${status}`;
}

export function parseApiErrorBody(payload: unknown, status: number): ApiError {
  const parsed = apiErrorBodySchema.safeParse(payload);
  const body: ApiErrorBody =
    parsed.success
      ? parsed.data
      : payload && typeof payload === "object"
        ? (payload as ApiErrorBody)
        : {};
  if (isDevEnvironment() && !parsed.success) {
    console.warn("API error body failed schema validation", z.treeifyError(parsed.error));
  }
  return new ApiError(extractApiErrorMessage(body, status), status, body);
}
