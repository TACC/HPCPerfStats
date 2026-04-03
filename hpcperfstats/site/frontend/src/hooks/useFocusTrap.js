import { useEffect } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]:not([disabled])",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"]):not([disabled])',
].join(", ");

function listFocusable(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (el) =>
      el.offsetParent !== null ||
      el === document.activeElement ||
      el.getClientRects().length > 0,
  );
}

/**
 * Keeps Tab / Shift+Tab focus inside `containerRef` while `active` is true.
 */
export function useFocusTrap(containerRef, active) {
  useEffect(() => {
    if (!active) return;

    function onKeyDown(e) {
      if (e.key !== "Tab") return;
      const root = containerRef.current;
      if (!root) return;

      const nodes = listFocusable(root);
      if (nodes.length === 0) return;

      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const cur = document.activeElement;

      e.preventDefault();
      if (e.shiftKey) {
        if (cur === first || !root.contains(cur)) {
          last.focus();
        } else {
          const i = nodes.indexOf(cur);
          const prev = i <= 0 ? last : nodes[i - 1];
          prev.focus();
        }
      } else if (cur === last || !root.contains(cur)) {
        first.focus();
      } else {
        const i = nodes.indexOf(cur);
        const next = i < 0 || i >= nodes.length - 1 ? first : nodes[i + 1];
        next.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [active, containerRef]);
}
