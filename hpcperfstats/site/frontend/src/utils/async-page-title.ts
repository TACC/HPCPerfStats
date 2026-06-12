export function buildAsyncPageTitle({
  loading,
  hasError,
  loadingTitle,
  readyTitle,
  fallbackTitle,
}: {
  loading: boolean;
  hasError: boolean;
  loadingTitle: string;
  readyTitle: string;
  fallbackTitle: string;
}): string {
  if (loading) return loadingTitle;
  if (!hasError && readyTitle) return readyTitle;
  return fallbackTitle;
}
