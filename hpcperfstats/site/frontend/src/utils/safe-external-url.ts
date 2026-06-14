/**
 * Allow only http/https URLs for user-facing external links from API payloads.
 */
export function isSafeHttpUrl(url: string | null | undefined): boolean {
  if (url == null || typeof url !== "string") return false;
  const trimmed = url.trim();
  if (!trimmed || trimmed.startsWith("//")) return false;
  try {
    const parsed = new URL(trimmed);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}
