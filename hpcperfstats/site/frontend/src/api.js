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
    window.location.href = "/login_prompt";
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
  getHomeOptions: () => request("/home/"),
  search: (params) => request("/search/?" + new URLSearchParams(params).toString()),
  getJobList: (params) => request("/jobs/?" + new URLSearchParams(params).toString()),
  getJobListHistograms: (params) =>
    request("/jobs/histograms/?" + new URLSearchParams(params).toString()),
  getJobDetail: (pk) => request(`/jobs/${encodeURIComponent(pk)}/`),
  getTypeDetail: (jid, typeName) =>
    request(`/jobs/${encodeURIComponent(jid)}/${encodeURIComponent(typeName)}/`),
  getHostPlot: (params) =>
    request("/host_plot/?" + new URLSearchParams(params).toString()),
  getAdminMonitor: () => request("/admin_monitor/"),
};

export default api;
