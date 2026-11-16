/**
 * @deprecated Legacy API facade - use Orval-generated hooks instead.
 * Retained for api.test.ts contract coverage only.
 */

import { customFetch } from "@/api/fetch-mutator";
import { orvalResponseData } from "@/api/orval-response";
import { API_PATHS, HISTOGRAM_EMBED_VERSION } from "@/api-paths";

function buildUrl(path: string, params?: URLSearchParams): string {
  const url = path.startsWith("/api") ? path : `/api${path}`;
  const query = params?.toString();
  return query ? `${url}?${query}` : url;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = buildUrl(path);
  const envelope = await customFetch<{ status: number; data: unknown }>(url, {
    method: options.method || "GET",
    ...options,
  });
  return orvalResponseData<T>(envelope) as T;
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
  rotateUserApiKey: () => request(API_PATHS.rotateUserApiKey, { method: "POST" }),
  getHomeOptions: () => request(API_PATHS.home),
  getJobMetricHistogram: (params: Record<string, string> | undefined, metric: string) =>
    request(
      buildUrl(
        API_PATHS.jobsHistograms,
        buildJobHistogramSearchParams(params, { group: "metric", metric }),
      ),
    ),
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
};
