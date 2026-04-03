import { useEffect, useRef } from "react";

/**
 * After client-side navigation, move focus to the first page h1 inside #main-content,
 * or to main itself, so screen-reader users hear the new context.
 * Skips the initial mount (no focus steal on first paint).
 */
export function useRouteFocusMain(pathname) {
  const prevPathRef = useRef(null);

  useEffect(() => {
    if (prevPathRef.current === null) {
      prevPathRef.current = pathname;
      return;
    }
    if (prevPathRef.current === pathname) return;
    prevPathRef.current = pathname;

    const id = window.requestAnimationFrame(() => {
      const main = document.getElementById("main-content");
      if (!main) return;
      const h1 = main.querySelector("h1");
      if (h1 instanceof HTMLElement) {
        if (!h1.hasAttribute("tabindex")) {
          h1.setAttribute("tabindex", "-1");
        }
        h1.focus({ preventScroll: false });
      } else {
        main.focus({ preventScroll: false });
      }
    });
    return () => window.cancelAnimationFrame(id);
  }, [pathname]);
}
