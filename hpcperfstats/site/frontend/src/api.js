/**
 * API client for HPCPerfStats Django REST backend.
 * All requests use credentials (cookies) for session auth.
 */

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

export const api = {
  getSession: () => request("/session/"),
  getUserApiKey: () => request("/user-api-key/"),
  rotateUserApiKey: () => request("/user-api-key/rotate/", { method: "POST" }),
  dropStaffForSession: () => request("/session/drop-staff/", { method: "POST" }),
  invalidateCacheForPage: (pagePath) =>
    request("/cache/invalidate-page/", {
      method: "POST",
      body: JSON.stringify({ page_path: pagePath }),
    }),
  getHomeOptions: () => request("/home/"),
  search: (params) => request("/search/?" + new URLSearchParams(params).toString()),
  getJobList: (params) => request("/jobs/?" + new URLSearchParams(params).toString()),
  /**
   * Queue-based histograms for a job list (jobs by queue, CPU hours by queue).
   * Uses the same filter params as getJobList, plus group=queue.
   */
  getJobQueueHistograms: (params) =>
    request(
      "/jobs/histograms/?" +
        new URLSearchParams({
          ...(params || {}),
          group: "queue",
          // Bust @dynamic_cache_page / CDN after server-side Bokeh embed changes.
          _histogram_embed_v: "3",
        }).toString()
    ),
  /**
   * Single metric histogram (thumb + full) for a job list.
   * Uses the same filter params as getJobList, plus group=metric&metric=<name>.
   */
  getJobMetricHistogram: (params, metric) =>
    request(
      "/jobs/histograms/?" +
        new URLSearchParams({
          ...(params || {}),
          group: "metric",
          metric,
          _histogram_embed_v: "3",
        }).toString()
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
  getHostPlot: (params) =>
    request("/host_plot/?" + new URLSearchParams(params).toString()),
  getAdminMonitorSection: (section, options = {}) => {
    const params = { section };
    if (options.refresh) params.refresh = "1";
    return request(`/admin_monitor/?${new URLSearchParams(params).toString()}`);
  },
  getJobMonitor: (days) => {
    const search = days
      ? `?${new URLSearchParams({ days: String(days) }).toString()}`
      : "";
    return request(`/job_monitor/${search}`);
  },
  getJobMonitorGpuForUser: (username, days) => {
    const params = new URLSearchParams({ username: String(username || "") });
    if (days) params.set("days", String(days));
    return request(`/job_monitor/gpu/?${params.toString()}`);
  },
};

export default api;
