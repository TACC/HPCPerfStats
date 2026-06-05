import { useState, useRef, useEffect, useLayoutEffect, useCallback, useId } from "react";
import { createPortal } from "react-dom";
import BokehPlotWithLimitation from "./BokehPlotWithLimitation";
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
 * (or Enter/Space on enlarge control), not hover. On mobile, full histogram only.
 */
function HistogramThumbnail({ index, title, plotItemThumb, plotItemFull, unavailableReason }) {
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const [popoverLayoutReady, setPopoverLayoutReady] = useState(false);
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
  const popoverTitleId = `hist-popover-title-${index}-${domSuffix}`;

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

  useEffect(() => {
    if (!expanded || isMobile) return;
    const main = document.getElementById("main-content");
    if (main) {
      main.inert = true;
    }
    return () => {
      if (main) {
        main.inert = false;
      }
    };
  }, [expanded, isMobile]);

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
          <BokehPlotWithLimitation
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

  const popover =
    showPopover && typeof document !== "undefined" ? (
      <div
        className="histogram-thumbnail-backdrop"
        role="presentation"
        onClick={collapseExpanded}
        onKeyDown={(e) => {
          if (e.key === "Escape") collapseExpanded();
        }}
      >
        <div
          ref={popoverRef}
          className="histogram-thumbnail-popover"
          role="dialog"
          aria-modal="true"
          aria-labelledby={popoverTitleId}
          onClick={(e) => e.stopPropagation()}
        >
          <div id={popoverTitleId} className="histogram-thumbnail-popover-title">
            <span className="histogram-thumbnail-popover-title-text">{title}</span>
            <button
              ref={closeButtonRef}
              type="button"
              className="btn btn-outline-secondary btn-sm histogram-thumbnail-close"
              onClick={(e) => {
                e.stopPropagation();
                collapseExpanded();
              }}
              aria-label="Close full size view"
            >
              Close
            </button>
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
            {hasOpened && popoverLayoutReady ? (
              <BokehPlotWithLimitation
                item={plotItemFull}
                id={fullId}
                plotName={`${title} (full)`}
                unavailableReason={unavailableReason}
                deferEmbedUntilVisible={false}
                intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
              />
            ) : null}
          </div>
        </div>
      </div>
    ) : null;

  return (
    <div className="histogram-thumbnail-wrapper">
      <div className="histogram-desktop-title">{title}</div>
      <div
        className="histogram-thumbnail-card"
        style={{ width: THUMB_SIZE.width }}
      >
        <div
          className="histogram-thumbnail histogram-thumbnail-shell"
          style={{
            width: THUMB_SIZE.width,
            height: THUMB_SIZE.height,
            border: "1px solid #dee2e6",
            borderRadius: "4px 4px 0 0",
            overflow: "hidden",
            backgroundColor: "#f8f9fa",
          }}
        >
          <BokehPlotWithLimitation
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
        <div className="histogram-thumbnail-actions">
          <button
            ref={thumbActivatorRef}
            type="button"
            aria-label={`${title}: enlarge chart`}
            aria-expanded={expanded}
            className="btn btn-outline-secondary btn-sm histogram-thumbnail-enlarge"
            onClick={handleThumbActivate}
          >
            Enlarge chart
          </button>
        </div>
      </div>
      {popover && document.body ? createPortal(popover, document.body) : null}
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
