import { useCallback, useEffect, useRef, useState } from "react";

const DEFAULT_HOVER_CLOSE_DELAY_MS = 120;

/** Debounced hover open state for portaled popovers (bridges trigger/panel gap). */
export function useDelayedHoverState(closeDelayMs = DEFAULT_HOVER_CLOSE_DELAY_MS) {
  const [hoverOpen, setHoverOpen] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelClose = useCallback(() => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const onHoverEnter = useCallback(() => {
    cancelClose();
    setHoverOpen(true);
  }, [cancelClose]);

  const onHoverLeave = useCallback(() => {
    cancelClose();
    closeTimerRef.current = setTimeout(() => {
      setHoverOpen(false);
      closeTimerRef.current = null;
    }, closeDelayMs);
  }, [cancelClose, closeDelayMs]);

  useEffect(
    () => () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    },
    [],
  );

  return { hoverOpen, onHoverEnter, onHoverLeave };
}
