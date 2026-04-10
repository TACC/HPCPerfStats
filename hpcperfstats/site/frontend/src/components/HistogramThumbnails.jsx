import { useState, useRef, useEffect, useLayoutEffect, useCallback, useId } from "react";
import BokehEmbed from "./BokehEmbed";
import LoadingMessage from "./LoadingMessage";
import { useFocusTrap } from "../hooks/useFocusTrap";

const THUMB_SIZE = { width: 280, height: 200 };
/** Slightly larger prefetch margin than BokehEmbed default so off-screen thumbs start loading sooner. */
const HISTOGRAM_INTERSECTION_ROOT_MARGIN = "120px 0px";
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
 * One histogram: on desktop, thumbnail with full-size popover opened by click
 * (or Enter/Space), not hover. On mobile, full histogram only, no popover.
 */
function HistogramThumbnail({ index, title, plotItemThumb, plotItemFull, unavailableReason }) {
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const [popoverLayoutReady, setPopoverLayoutReady] = useState(false);
  const wrapperRef = useRef(null);
  const thumbActivatorRef = useRef(null);
  const popoverRef = useRef(null);
  const popoverPlotRef = useRef(null);
  const closeButtonRef = useRef(null);
  const domSuffix = useId().replace(/:/g, "");

  const showPopover = !isMobile && expanded;
  const trapPopoverFocus = !isMobile && expanded && showPopover;
  useFocusTrap(popoverRef, trapPopoverFocus);

  const thumbId = `hist-thumb-${index}-${domSuffix}`;
  const fullId = `hist-full-${index}-${domSuffix}`;

  const collapseExpanded = useCallback(() => {
    setExpanded(false);
    window.requestAnimationFrame(() => {
      thumbActivatorRef.current?.focus();
    });
  }, []);

  const handleThumbActivate = () => {
    setExpanded((prev) => !prev);
    setHasOpened(true);
  };

  const handleClick = (e) => {
    e.stopPropagation();
    handleThumbActivate();
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleThumbActivate();
    }
    if (e.key === "Escape" && expanded) {
      e.preventDefault();
      collapseExpanded();
    }
  };

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

  useLayoutEffect(() => {
    if (!expanded || !showPopover || isMobile) {
      setPopoverLayoutReady(false);
      return;
    }
    function measure() {
      const node = popoverPlotRef.current;
      return !!(node && node.offsetWidth > 0 && node.offsetHeight > 0);
    }
    if (measure()) {
      setPopoverLayoutReady(true);
      return;
    }
    setPopoverLayoutReady(false);
    let cancelled = false;
    let raf2 = null;
    const raf1 = window.requestAnimationFrame(() => {
      if (cancelled) return;
      if (measure()) {
        setPopoverLayoutReady(true);
        return;
      }
      raf2 = window.requestAnimationFrame(() => {
        if (!cancelled) setPopoverLayoutReady(true);
      });
    });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf1);
      if (raf2 != null) window.cancelAnimationFrame(raf2);
    };
  }, [expanded, showPopover, isMobile]);

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
            deferEmbedUntilVisible={false}
            intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
          />
        </div>
      </div>
    );
  }

  return (
    <div ref={wrapperRef} className="histogram-thumbnail-wrapper" style={{ position: "relative" }}>
      <div className="histogram-desktop-title">{title}</div>
      <div
        ref={thumbActivatorRef}
        role="button"
        tabIndex={0}
        aria-label={`${title}: enlarge chart (click, Enter, or Space to open or close)`}
        aria-expanded={expanded}
        className="histogram-thumbnail histogram-thumbnail-shell"
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
          embedMinHeightPx={THUMB_SIZE.height}
          deferEmbedUntilVisible={false}
          wrapperClassName="histogram-thumbnail-bokeh"
          intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
        />
      </div>
      {showPopover && (
        <div
          ref={popoverRef}
          className="histogram-thumbnail-popover"
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
            ref={popoverPlotRef}
            className="histogram-thumbnail-popover-plot"
            style={{
              width: 600,
              height: 400,
              backgroundColor: "#fff",
              border: "1px solid #dee2e6",
              borderRadius: 4,
            }}
          >
            {hasOpened && popoverLayoutReady && (
              <BokehEmbed
                item={plotItemFull}
                id={fullId}
                plotName={`${title} (full)`}
                unavailableReason={unavailableReason}
                deferEmbedUntilVisible={false}
                intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
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
          key={`job-list-hist-${i}`}
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
