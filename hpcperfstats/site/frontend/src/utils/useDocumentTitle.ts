import { useEffect } from "react";

const DEFAULT_SUFFIX = "HPCPerfStats";

export type UseDocumentTitleOptions = {
  suffix?: string;
};

/** Sets document.title to "{title} | {suffix}" for the lifetime of the component. */
export function useDocumentTitle(title: string, options: UseDocumentTitleOptions = {}) {
  const suffix = options.suffix ?? DEFAULT_SUFFIX;

  useEffect(() => {
    const segment = title && String(title).trim() ? String(title).trim() : null;
    document.title = segment ? `${segment} | ${suffix}` : suffix;
  }, [title, suffix]);
}
