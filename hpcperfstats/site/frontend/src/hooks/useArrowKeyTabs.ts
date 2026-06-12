import { useCallback } from "react";

/**
 * WAI-ARIA tabs optional keyboard pattern: ArrowLeft/Right, Home, End move focus and selection.
 */
export function useArrowKeyTabs(
  tabButtonIds: string[],
  activeTabId: string,
  onSelectTab: (nextTabId: string) => void,
) {
  return useCallback(
    (event: React.KeyboardEvent, tabButtonId: string) => {
      const ids = tabButtonIds.filter(Boolean);
      const index = ids.indexOf(tabButtonId);
      if (index < 0) return;

      let nextIndex = index;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        event.preventDefault();
        nextIndex = (index + 1) % ids.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        event.preventDefault();
        nextIndex = (index - 1 + ids.length) % ids.length;
      } else if (event.key === "Home") {
        event.preventDefault();
        nextIndex = 0;
      } else if (event.key === "End") {
        event.preventDefault();
        nextIndex = ids.length - 1;
      } else {
        return;
      }

      const nextId = ids[nextIndex];
      onSelectTab(nextId);
      window.requestAnimationFrame(() => {
        document.getElementById(nextId)?.focus();
      });
    },
    [tabButtonIds, onSelectTab],
  );
}
