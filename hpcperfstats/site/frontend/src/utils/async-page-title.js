export function buildAsyncPageTitle({
  loading,
  hasError,
  loadingTitle,
  readyTitle,
  fallbackTitle,
}) {
  if (loading) return loadingTitle;
  if (!hasError && readyTitle) return readyTitle;
  return fallbackTitle;
}
