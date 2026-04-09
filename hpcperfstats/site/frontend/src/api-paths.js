export const API_PATHS = {
  session: "/session/",
  userApiKey: "/user-api-key/",
  rotateUserApiKey: "/user-api-key/rotate/",
  dropStaffForSession: "/session/drop-staff/",
  invalidateCacheForPage: "/cache/invalidate-page/",
  home: "/home/",
  search: "/search/",
  jobs: "/jobs/",
  jobsHistograms: "/jobs/histograms/",
  hostPlot: "/host_plot/",
  adminMonitor: "/admin_monitor/",
  jobMonitor: "/job_monitor/",
  jobMonitorGpu: "/job_monitor/gpu/",
};

// Bump when histogram embed safety behavior changes so cached API responses
// with older Bokeh json_item payloads are bypassed.
export const HISTOGRAM_EMBED_VERSION = "4";
