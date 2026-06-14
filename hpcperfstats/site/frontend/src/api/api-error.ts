import { z } from "zod";

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

export function extractApiErrorMessage(body: ApiErrorBody, status: number): string {
  if (typeof body.error === "string" && body.error.trim()) return body.error;
  if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  if (typeof body.message === "string" && body.message.trim()) return body.message;
  return `HTTP ${status}`;
}

export function parseApiErrorBody(payload: unknown, status: number): ApiError {
  const parsed = apiErrorBodySchema.safeParse(payload);
  const body: ApiErrorBody =
    parsed.success
      ? parsed.data
      : payload && typeof payload === "object"
        ? (payload as ApiErrorBody)
        : {};
  if (import.meta.env.DEV && !parsed.success) {
    // eslint-disable-next-line no-console
    console.warn("API error body failed schema validation", parsed.error.flatten());
  }
  return new ApiError(extractApiErrorMessage(body, status), status, body);
}
