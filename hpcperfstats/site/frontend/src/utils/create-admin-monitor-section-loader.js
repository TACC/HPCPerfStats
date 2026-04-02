/**
 * Factory for admin_monitor section loaders (shared request / loading / error handling).
 *
 * @param {object} opts
 * @param {string} opts.section - Section id passed to getAdminMonitorSection
 * @param {(res: object) => unknown} opts.pickResponse - Map API JSON to stored state
 * @param {(loading: boolean) => void} opts.setLoading
 * @param {(message: string | null) => void} opts.setError
 * @param {(data: unknown) => void} opts.setData
 * @param {{ getAdminMonitorSection: Function }} opts.apiClient - typically ../api `api`
 */
export function createAdminMonitorSectionLoader(opts) {
  const { section, pickResponse, setLoading, setError, setData, apiClient } = opts;
  return function loadAdminMonitorSection(forceRefresh = false) {
    setLoading(true);
    setError(null);
    return apiClient
      .getAdminMonitorSection(section, { refresh: forceRefresh })
      .then((res) => setData(pickResponse(res)))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
}
