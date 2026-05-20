import { api } from "../api";

/**
 * Fetch known compute host names for the live node heatmap.
 * Uses GET /api/admin_monitor/?section=hosts — the canonical source of
 * per-host last-seen data from host_data.
 *
 * @param {{ refresh?: boolean }} [options]
 * @returns {Promise<Array<{ host: string, last_time?: string, age_bucket?: string }>>}
 */
export async function fetchHeatmapKnownHosts({ refresh = false } = {}) {
  const res = await api.getAdminMonitorSection("hosts", { refresh });
  const stats = res.host_stats || [];
  return stats.filter(
    (h) => h && typeof h.host === "string" && h.host.includes("."),
  );
}
