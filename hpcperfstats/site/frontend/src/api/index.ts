/**
 * Legacy API facade — delegates to customFetch mutator (Orval-compatible).
 * Prefer Orval-generated react-query hooks for new code.
 */

import { customFetch } from "./fetch-mutator";
import { API_PATHS, HISTOGRAM_EMBED_VERSION } from "../api-paths";

type QueryParams = Record<string, string | number | boolean | undefined | null> | URLSearchParams;

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = path.startsWith("/api") ? path : `/api${path}`;
  return customFetch<T>({ url, method: options.method || "GET" }, options);
}

function requestWithQuery<T>(path: string, params?: QueryParams): Promise<T> {
  const url = path.startsWith("/api") ? path : `/api${path}`;
  if (params instanceof URLSearchParams) {
    const query = params.toString();
    return customFetch<T>({ url: query ? `${url}?${query}` : url, method: "GET" });
  }
  return customFetch<T>({
    url,
    method: "GET",
    params: params as Record<string, unknown> | undefined,
  });
}

function buildJobHistogramSearchParams(
  params: Record<string, string> | undefined,
  { group, metric }: { group: string; metric?: string },
) {
  const query: Record<string, string> = {
    ...(params || {}),
    group,
    _histogram_embed_v: HISTOGRAM_EMBED_VERSION,
  };
  if (metric) query.metric = metric;
  return new URLSearchParams(query);
}

export const api = {
  getSession: () => request(API_PATHS.session),
  getUserApiKey: () => request(API_PATHS.userApiKey),
  rotateUserApiKey: () => request(API_PATHS.rotateUserApiKey, { method: "POST" }),
  dropStaffForSession: () => request(API_PATHS.dropStaffForSession, { method: "POST" }),
  invalidateCacheForPage: (pagePath: string) =>
    request(API_PATHS.invalidateCacheForPage, {
      method: "POST",
      body: JSON.stringify({ page_path: pagePath }),
    }),
  getHomeOptions: () => request(API_PATHS.home),
  getJobList: (params?: QueryParams) => requestWithQuery(API_PATHS.jobs, params),
  getJobMetricHistogram: (params: Record<string, string> | undefined, metric: string) =>
    requestWithQuery(
      API_PATHS.jobsHistograms,
      buildJobHistogramSearchParams(params, { group: "metric", metric }),
    ),
  getJobDetail: (pk: string | number) => request(`/jobs/${encodeURIComponent(String(pk))}/`),
  getJobDetailLight: (pk: string | number) => request(`/jobs/${encodeURIComponent(String(pk))}/?light=1`),
  getJobPlots: (
    pk: string | number,
    plot: string | null = null,
    zoom = false,
    progressive = false,
  ) => {
    const params = new URLSearchParams();
    if (plot) params.set("plot", plot);
    if (zoom) params.set("zoom", "1");
    if (progressive) params.set("progressive", "1");
    const queryString = params.toString();
    const suffix = queryString ? `?${queryString}` : "";
    return request(`/jobs/${encodeURIComponent(String(pk))}/plots/${suffix}`);
  },
  getTypeDetail: (jid: string | number, typeName: string) =>
    request(`/jobs/${encodeURIComponent(String(jid))}/${encodeURIComponent(typeName)}/`),
  getHostPlot: (params?: QueryParams) => requestWithQuery(API_PATHS.hostPlot, params),
  getAdminMonitorSection: (section: string, options: { refresh?: boolean } = {}) => {
    const params: Record<string, string> = { section };
    if (options.refresh) params.refresh = "1";
    return requestWithQuery(API_PATHS.adminMonitor, params);
  },
  getJobMonitor: (days?: number | string) =>
    requestWithQuery(API_PATHS.jobMonitor, days ? { days: String(days) } : {}),
  getJobMonitorGpuForUser: (username: string, days?: number | string) => {
    const params: Record<string, string> = { username: String(username || "") };
    if (days) params.days = String(days);
    return requestWithQuery(API_PATHS.jobMonitorGpu, params);
  },
};

