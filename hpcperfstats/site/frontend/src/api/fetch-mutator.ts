/**
 * Orval mutator: session cookie auth, CSRF header, 401 → login_prompt redirect.
 */

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
  const csrfToken = getCookie("csrftoken");
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
    credentials: "include",
    headers,
    body: data !== undefined ? JSON.stringify(data) : options.body,
    signal: signal ?? options.signal,
  });

  if (res.status === 401) {
    const next = encodeURIComponent(
      typeof window !== "undefined"
        ? window.location.pathname + window.location.search
        : "",
    );
    if (typeof window !== "undefined") {
      window.location.href = next ? `/login_prompt?next=${next}` : "/login_prompt";
    }
    throw new Error("Unauthorized");
  }

  const payload = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    const message =
      (typeof payload.error === "string" && payload.error) ||
      (typeof payload.detail === "string" && payload.detail) ||
      `HTTP ${res.status}`;
    throw new Error(message);
  }
  return payload as T;
}

/** Anonymous public cluster dashboard — credentials omitted. */
export async function fetchPubClusterDashboard<T = unknown>(): Promise<T> {
  const res = await fetch("/api/pub/cluster-dashboard/", {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "omit",
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    const message =
      (typeof data.detail === "string" && data.detail) ||
      (typeof data.error === "string" && data.error) ||
      `HTTP ${res.status}`;
    throw new Error(message);
  }
  return data as T;
}
