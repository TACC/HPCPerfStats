import { useState, useRef, useEffect, useCallback } from "react";
import BokehEmbed from "./BokehEmbed";
import LoadingMessage from "./LoadingMessage";
import { useFocusTrap } from "../hooks/useFocusTrap";

const THUMB_SIZE = { width: 280, height: 200 };
const MOBILE_BREAKPOINT = 768;

function useIsMobile() {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== "undefined" && window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 0.02}px)`).matches
  );
  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 0.02}px)`);
    const handler = () => setIsMobile(mql.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);
  return isMobile;
}

/**
 * One histogram: on desktop, thumbnail with full-size popover on hover/click.
 * On mobile, full histogram only, no popover, sized for viewport.
 */
function HistogramThumbnail({ index, title, plotItemThumb, plotItemFull, unavailableReason }) {
  const isMobile = useIsMobile();
  const [hovered, setHovered] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const wrapperRef = useRef(null);
  const leaveTimerRef = useRef(null);
  const thumbActivatorRef = useRef(null);
  const popoverRef = useRef(null);
  const closeButtonRef = useRef(null);

  const showPopover = !isMobile && (hovered || expanded);
  const trapPopoverFocus = !isMobile && expanded && showPopover;
  useFocusTrap(popoverRef, trapPopoverFocus);

  const thumbId = `hist-thumb-${index}`;
  const fullId = `hist-full-${index}`;

  const handleMouseEnter = () => {
    if (leaveTimerRef.current) {
      clearTimeout(leaveTimerRef.current);
      leaveTimerRef.current = null;
    }
    setHovered(true);
    setHasOpened(true);
  };

  const handleMouseLeave = () => {
    leaveTimerRef.current = setTimeout(() => setHovered(false), 150);
  };

  const collapseExpanded = useCallback(() => {
    setExpanded(false);
    window.requestAnimationFrame(() => {
      thumbActivatorRef.current?.focus();
    });
  }, []);

  const handleClick = () => {
    setExpanded((prev) => !prev);
    setHasOpened(true);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setExpanded((prev) => !prev);
      setHasOpened(true);
    }
    if (e.key === "Escape" && expanded) {
      e.preventDefault();
      collapseExpanded();
    }
  };

  useEffect(() => {
    return () => {
      if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!expanded || !showPopover || isMobile) return;
    const id = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [expanded, showPopover, isMobile]);

  useEffect(() => {
    if (!expanded) return;
    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        collapseExpanded();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [expanded, collapseExpanded]);

  /* Mobile: full histogram only, no popover, container sized for viewport */
  if (isMobile) {
    return (
      <div className="histogram-thumbnail-wrapper histogram-mobile">
        <div className="histogram-mobile-title">{title}</div>
        <div className="histogram-mobile-plot">
          <BokehEmbed
            item={plotItemFull}
            id={fullId}
            plotName={title}
            unavailableReason={unavailableReason}
          />
        </div>
      </div>
    );
  }

  return (
    <div
      ref={wrapperRef}
      className="histogram-thumbnail-wrapper"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      style={{ position: "relative" }}
    >
      <div className="histogram-desktop-title">{title}</div>
      <div
        ref={thumbActivatorRef}
        role="button"
        tabIndex={0}
        aria-label={`${title}: enlarge chart (click, Enter, or Space)`}
        aria-expanded={expanded}
        className="histogram-thumbnail"
        style={{
          width: THUMB_SIZE.width,
          height: THUMB_SIZE.height,
          border: "1px solid #dee2e6",
          borderRadius: 4,
          overflow: "hidden",
          backgroundColor: "#f8f9fa",
          cursor: "pointer",
        }}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
      >
        <BokehEmbed
          item={plotItemThumb}
          id={thumbId}
          plotName={title}
          unavailableReason={unavailableReason}
        />
      </div>
      {showPopover && (
        <div
          ref={popoverRef}
          className="histogram-thumbnail-popover"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          role="dialog"
          aria-modal={expanded ? "true" : undefined}
          aria-label={`Full size: ${title}`}
        >
          <div className="histogram-thumbnail-popover-title">
            {title}
            {expanded && (
              <button
                ref={closeButtonRef}
                type="button"
                className="histogram-thumbnail-close"
                onClick={() => collapseExpanded()}
                aria-label="Close full size view"
                style={{
                  marginLeft: 8,
                  padding: "2px 8px",
                  fontSize: "0.875rem",
                }}
              >
                Close
              </button>
            )}
          </div>
          <div
            className="histogram-thumbnail-popover-plot"
            style={{
              width: 600,
              height: 400,
              backgroundColor: "#fff",
              border: "1px solid #dee2e6",
              borderRadius: 4,
            }}
          >
            {hasOpened && (
              <BokehEmbed
                item={plotItemFull}
                id={fullId}
                plotName={`${title} (full)`}
                unavailableReason={unavailableReason}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function HistogramThumbnails({ histograms }) {
  if (!histograms) {
    return (
      <div
        style={{
          minHeight: 120,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#666",
          backgroundColor: "#f8f9fa",
          border: "1px dashed #dee2e6",
          borderRadius: 4,
          padding: 12,
        }}
      >
        <LoadingMessage message="Loading histograms…" />
      </div>
    );
  }

  if (Array.isArray(histograms) && histograms.length === 0) {
    return null;
  }

  return (
    <section
      className="histogram-thumbnails-grid"
      aria-label="Histogram charts for this job list"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
        gap: 16,
        justifyContent: "center",
      }}
    >
      {histograms.map((h, i) => (
        <HistogramThumbnail
          key={h.title}
          index={i}
          title={h.title}
          plotItemThumb={h.plot_item_thumb}
          plotItemFull={h.plot_item_full}
          unavailableReason={h.plot_unavailable_reason}
        />
      ))}
    </section>
  );
}
