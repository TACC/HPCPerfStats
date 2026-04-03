import { useEffect } from "react";

const DEFAULT_SUFFIX = "HPCPerfStats";

/**
 * Sets document.title to "{title} | {suffix}" for the lifetime of the component.
 *
 * @param {string} title — Page-specific title segment (omit empty strings).
 * @param {{ suffix?: string }} [options]
 */
export function useDocumentTitle(title, options = {}) {
  const suffix = options.suffix ?? DEFAULT_SUFFIX;

  useEffect(() => {
    const segment = title && String(title).trim() ? String(title).trim() : null;
    document.title = segment ? `${segment} | ${suffix}` : suffix;
  }, [title, suffix]);
}
