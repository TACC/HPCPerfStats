import { useState, useRef, useEffect } from "react";
import BokehEmbed from "./BokehEmbed";
import LoadingMessage from "./LoadingMessage";

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
function HistogramThumbnail({ index, title, plotItemThumb, plotItemFull }) {
  const isMobile = useIsMobile();
  const [hovered, setHovered] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const wrapperRef = useRef(null);
  const leaveTimerRef = useRef(null);

  const thumbId = `hist-thumb-${index}`;
  const fullId = `hist-full-${index}`;
  const showPopover = !isMobile && (hovered || expanded);

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
      setExpanded(false);
    }
  };

  useEffect(() => {
    return () => {
      if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!expanded) return;
    const onKeyDown = (e) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expanded]);

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
      <div
        role="button"
        tabIndex={0}
        aria-label={`${title}, click or press Enter to view full size`}
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
        />
      </div>
      {showPopover && (
        <div
          className="histogram-thumbnail-popover"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          role="dialog"
          aria-label={`Full size: ${title}`}
        >
          <div className="histogram-thumbnail-popover-title">
            {title}
            {expanded && (
              <button
                type="button"
                className="histogram-thumbnail-close"
                onClick={() => setExpanded(false)}
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
    <div
      className="histogram-thumbnails-grid"
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
        />
      ))}
    </div>
  );
}
