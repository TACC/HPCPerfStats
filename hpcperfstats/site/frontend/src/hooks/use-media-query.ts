import { useEffect, useState } from "react";

/**
 * SSR-safe media query hook: returns `defaultMatches` until mounted, then tracks live matches.
 */
export function useMediaQuery(query: string, defaultMatches = false): boolean {
  const [matches, setMatches] = useState(defaultMatches);

  useEffect(() => {
    const mql = window.matchMedia(query);
    const update = () => setMatches(mql.matches);
    update();
    mql.addEventListener("change", update);
    return () => mql.removeEventListener("change", update);
  }, [query]);

  return matches;
}

export function useIsMobile(breakpointPx = 991.98): boolean {
  return useMediaQuery(`(max-width: ${breakpointPx}px)`, false);
}

export function useMinWidth(breakpointPx: number): boolean {
  return useMediaQuery(`(min-width: ${breakpointPx}px)`, false);
}
