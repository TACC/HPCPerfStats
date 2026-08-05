/**
 * Stable fingerprint for Bokeh `json_item` payloads.
 * Used to skip remounts when semantic plot content is unchanged across polls/refetches.
 */
export function fingerprintBokehJsonItem(item: unknown): string {
  if (item == null) return "";
  try {
    return JSON.stringify(item);
  } catch {
    return String(item);
  }
}
