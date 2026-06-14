/**
 * Orval mutator: session cookie auth, CSRF header, 401 → login_prompt redirect.
 */

import { ApiError, parseApiErrorBody } from "./api-error";
import { parseApiResponse } from "./parse-api-response";

export type ErrorType<T> = T;

export type OrvalRequestConfig = {
  url: string;
  method: string;
  params?: Record<string, unknown>;
  data?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

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

function buildUrl(path: string, params?: Record<string, unknown>): string {
  const base = path.startsWith("http") ? path : path;
  if (!params) return base;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `${base}?${qs}` : base;
}

export async function customFetch<T>(
  config: OrvalRequestConfig,
  options: RequestInit = {},
): Promise<T> {
  const { url, method, params, data, headers: configHeaders, signal } = config;
  const upperMethod = (method || "GET").toUpperCase();
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
    ...(configHeaders || {}),
    ...(options.headers as Record<string, string> | undefined),
  };
  if (csrfToken) headers["X-CSRFToken"] = csrfToken;

  const res = await fetch(buildUrl(url, params), {
    ...options,
    method,
    credentials: options.credentials ?? "include",
    headers,
    body: data !== undefined ? JSON.stringify(data) : options.body,
    signal: signal ?? options.signal,
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
  return parseApiResponse<T>(upperMethod, url, payload);
}

/** Anonymous public cluster dashboard — credentials omitted. */
export async function fetchPubClusterDashboard<T = unknown>(
  params?: Record<string, unknown>,
): Promise<T> {
  return customFetch<T>(
    { url: "/api/pub/cluster-dashboard/", method: "GET", params },
    { credentials: "omit" },
  );
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
