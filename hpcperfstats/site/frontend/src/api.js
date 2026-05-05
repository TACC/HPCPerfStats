/**
 * API client for HPCPerfStats Django REST backend.
 * All requests use credentials (cookies) for session auth.
 */

import { API_PATHS, HISTOGRAM_EMBED_VERSION } from "./api-paths";

const API_BASE = "/api";

function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== "") {
    const parts = document.cookie.split(";");
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i].trim();
      if (part.startsWith(name + "=")) {
        cookieValue = decodeURIComponent(part.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}

async function request(path, options = {}) {
  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
  const csrfToken = getCookie("csrftoken");
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...options.headers,
  };
  if (csrfToken) headers["X-CSRFToken"] = csrfToken;
  const res = await fetch(url, {
    ...options,
    credentials: "include",
    headers,
  });
  if (res.status === 401) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = next ? `/login_prompt?next=${next}` : "/login_prompt";
    throw new Error("Unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || data.detail || `HTTP ${res.status}`);
  }
  return data;
}

function requestWithQuery(path, params) {
  const query = new URLSearchParams(params || {}).toString();
  return request(query ? `${path}?${query}` : path);
}

export const api = {
  getSession: () => request(API_PATHS.session),
  getUserApiKey: () => request(API_PATHS.userApiKey),
  rotateUserApiKey: () => request(API_PATHS.rotateUserApiKey, { method: "POST" }),
  dropStaffForSession: () => request(API_PATHS.dropStaffForSession, { method: "POST" }),
  invalidateCacheForPage: (pagePath) =>
    request(API_PATHS.invalidateCacheForPage, {
      method: "POST",
      body: JSON.stringify({ page_path: pagePath }),
    }),
  getHomeOptions: () => request(API_PATHS.home),
  search: (params) => requestWithQuery(API_PATHS.search, params),
  getJobList: (params) => requestWithQuery(API_PATHS.jobs, params),
  /**
   * Single metric histogram (thumb + full) for a job list.
   * Uses the same filter params as getJobList, plus group=metric&metric=<name>.
   */
  getJobMetricHistogram: (params, metric) =>
    requestWithQuery(
      API_PATHS.jobsHistograms,
      buildJobHistogramSearchParams(params, { group: "metric", metric }),
    ),
  getJobDetail: (pk) => request(`/jobs/${encodeURIComponent(pk)}/`),
  getJobDetailLight: (pk) => request(`/jobs/${encodeURIComponent(pk)}/?light=1`),
  getJobPlots: (pk, plot = null, zoom = false, progressive = false) => {
    const params = new URLSearchParams();
    if (plot) params.set("plot", plot);
    if (zoom) params.set("zoom", "1");
    if (progressive) params.set("progressive", "1");
    const queryString = params.toString();
    const suffix = queryString ? `?${queryString}` : "";
    return request(`/jobs/${encodeURIComponent(pk)}/plots/${suffix}`);
  },
  getTypeDetail: (jid, typeName) =>
    request(`/jobs/${encodeURIComponent(jid)}/${encodeURIComponent(typeName)}/`),
  getHostPlot: (params) => requestWithQuery(API_PATHS.hostPlot, params),
  getAdminMonitorSection: (section, options = {}) => {
    const params = { section };
    if (options.refresh) params.refresh = "1";
    return requestWithQuery(API_PATHS.adminMonitor, params);
  },
  getJobMonitor: (days) => {
    return requestWithQuery(API_PATHS.jobMonitor, days ? { days: String(days) } : {});
  },
  getJobMonitorGpuForUser: (username, days) => {
    const params = new URLSearchParams({ username: String(username || "") });
    if (days) params.set("days", String(days));
    return requestWithQuery(API_PATHS.jobMonitorGpu, params);
  },
};

function buildJobHistogramSearchParams(params, { group, metric }) {
  const query = {
    ...(params || {}),
    group,
    _histogram_embed_v: HISTOGRAM_EMBED_VERSION,
  };
  if (metric) query.metric = metric;
  return new URLSearchParams(query);
}

export default api;
