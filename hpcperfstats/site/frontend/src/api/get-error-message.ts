import { ApiError, type ApiErrorBody } from "./api-error";

export function getApiBody(value: unknown): ApiErrorBody {
  return value && typeof value === "object" ? (value as ApiErrorBody) : {};
}

export function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message.trim()) return error.message;
  const body = getApiBody(error);
  if (typeof body.error === "string" && body.error.trim()) return body.error;
  if (typeof body.message === "string" && body.message.trim()) return body.message;
  if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  return fallback;
}

export function getApiErrorStatus(error: unknown): number | undefined {
  return error instanceof ApiError ? error.status : undefined;
}

/** Status-aware copy for page-level error banners. */
export function getStatusAwareErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return "You don't have permission to view this resource.";
    }
    if (error.status === 404) {
      return error.message || "The requested resource was not found.";
    }
    if (error.status === 429) {
      return "Too many requests. Please wait a moment and try again.";
    }
    if (error.status >= 500) {
      return error.message || "A server error occurred. Please try again later.";
    }
  }
  return getErrorMessage(error, fallback);
}
