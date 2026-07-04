/**
 * Orval mutator: session cookie auth, CSRF header, 401 → login_prompt redirect.
 * Orval 8 signature: (url, RequestInit) → { status, data, headers }.
 */

import { ApiError, parseApiErrorBody } from "./api-error";
import { parseApiResponse } from "./parse-api-response";
import { orvalResponseData } from "./orval-response";

export type ErrorType<T> = T;

function getCookie(name: string): string | null {
  if (typeof document === "undefined" || !document.cookie) return null;
  const parts = document.cookie.split(";");
  for (const part of parts) {
    const trimmed = part.trim();
    if (trimmed.startsWith(`${name}=`)) {
      return decodeURIComponent(trimmed.substring(name.length + 1));
    }
  }
  return null;
}

function appendSearchParams(url: string, params?: Record<string, unknown>): string {
  if (!params) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  if (!qs) return url;
  return url.includes("?") ? `${url}&${qs}` : `${url}?${qs}`;
}

export async function customFetch<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const upperMethod = (options.method || "GET").toUpperCase();
  const csrfToken = getCookie("csrftoken");
  if (
    ["POST", "PUT", "PATCH", "DELETE"].includes(upperMethod) &&
    !csrfToken
  ) {
    throw new Error("CSRF token missing");
  }
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (csrfToken) headers["X-CSRFToken"] = csrfToken;

  const res = await fetch(url, {
    ...options,
    credentials: options.credentials ?? "include",
    headers,
  });

  const payload = (await res.json().catch(() => ({}))) as Record<string, unknown>;

  if (res.status === 401) {
    const next = encodeURIComponent(
      typeof window !== "undefined"
        ? window.location.pathname + window.location.search
        : "",
    );
    if (typeof window !== "undefined") {
      window.location.href = next ? `/login_prompt?next=${next}` : "/login_prompt";
    }
    throw new ApiError("Unauthorized", 401, parseApiErrorBody(payload, 401).body);
  }

  if (!res.ok) {
    throw parseApiErrorBody(payload, res.status);
  }

  const validated = parseApiResponse<unknown>(upperMethod, url, payload);
  return {
    status: res.status,
    data: validated,
    headers: res.headers,
  } as T;
}

/** Anonymous public cluster dashboard — credentials omitted. */
export async function fetchPubClusterDashboard<T = unknown>(
  params?: Record<string, unknown>,
): Promise<T> {
  const envelope = await customFetch<{ status: number; data: unknown }>(
    appendSearchParams("/api/pub/cluster-dashboard/", params),
    {
      method: "GET",
      credentials: "omit",
    },
  );
  return orvalResponseData<T>(envelope) as T;
}

/** Lazy-load one expansion-factor histogram period. */
export async function fetchPubExpansionPeriod<T = unknown>(
  grouping: "yearly" | "monthly",
  period: string,
): Promise<T> {
  return fetchPubClusterDashboard<T>({
    section: "expansion_factor",
    grouping,
    period,
  });
}
