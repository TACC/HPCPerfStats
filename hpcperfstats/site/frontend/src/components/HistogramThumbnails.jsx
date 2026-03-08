import { useState, useRef, useEffect } from "react";
import BokehEmbed from "./BokehEmbed";

const THUMB_SIZE = { width: 280, height: 200 };

/**
 * One histogram as a medium thumbnail; shows full-size Bokeh plot in a popover on hover.
 */
function HistogramThumbnail({ index, title, plotItemThumb, plotItemFull }) {
  const [hovered, setHovered] = useState(false);
  const [hasHovered, setHasHovered] = useState(false);
  const wrapperRef = useRef(null);
  const popoverRef = useRef(null);
  const leaveTimerRef = useRef(null);

  const thumbId = `hist-thumb-${index}`;
  const fullId = `hist-full-${index}`;

  const handleMouseEnter = () => {
    if (leaveTimerRef.current) {
      clearTimeout(leaveTimerRef.current);
      leaveTimerRef.current = null;
    }
    setHovered(true);
    setHasHovered(true);
  };

  const handleMouseLeave = () => {
    leaveTimerRef.current = setTimeout(() => setHovered(false), 150);
  };

  useEffect(() => {
    return () => {
      if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
    };
  }, []);

  return (
    <div
      ref={wrapperRef}
      className="histogram-thumbnail-wrapper"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      style={{ position: "relative" }}
    >
      <div
        className="histogram-thumbnail"
        style={{
          width: THUMB_SIZE.width,
          height: THUMB_SIZE.height,
          border: "1px solid #dee2e6",
          borderRadius: 4,
          overflow: "hidden",
          backgroundColor: "#f8f9fa",
        }}
      >
        <BokehEmbed
          item={plotItemThumb}
          id={thumbId}
          plotName={title}
        />
      </div>
      {hovered && (
        <div
          ref={popoverRef}
          className="histogram-thumbnail-popover"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <div className="histogram-thumbnail-popover-title">{title}</div>
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
            {hasHovered && (
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

/**
 * Grid of medium-sized histogram thumbnails; each shows the full plot in a popover on mouse over.
 */
export default function HistogramThumbnails({ histograms }) {
  if (!histograms || histograms.length === 0) {
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
        No histogram data available for this job list.
      </div>
    );
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
