import { useEffect, useRef, useState } from "react";
import { useSession } from "../session-context";

/** Poll until window.Bokeh is defined (Bokeh JS loaded), then resolve. */
function whenBokehReady(timeoutMs = 10000) {
  if (typeof window !== "undefined" && window.Bokeh) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    const t = setInterval(() => {
      if (typeof window !== "undefined" && window.Bokeh) {
        clearInterval(t);
        resolve();
        return;
      }
      if (Date.now() > deadline) {
        clearInterval(t);
        reject(new Error("Bokeh JS did not load in time"));
      }
    }, 50);
  });
}

function maximizeEmbeddedPlot(targetId) {
  const targetEl = typeof document !== "undefined" ? document.getElementById(targetId) : null;
  if (!targetEl) return;

  // Make the embedded root fill the available zoom container area.
  const rootEl = targetEl.querySelector(".bk-root");
  if (rootEl) {
    rootEl.style.width = "100%";
    rootEl.style.height = "100%";
    rootEl.style.maxWidth = "none";
  }

  // Ask Bokeh models in this target to use stretch sizing.
  try {
    const index = window.Bokeh?.index || {};
    Object.values(index).forEach((view) => {
      if (!view?.el || !targetEl.contains(view.el) || !view?.model) return;
      view.model.sizing_mode = "stretch_both";
      view.model.width_policy = "max";
      view.model.height_policy = "max";
      if (typeof view.request_render === "function") view.request_render();
    });
  } catch {
    // Best-effort sizing only.
  }

  // Trigger a layout pass after styles/model hints are applied.
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("resize"));
  }
}

const PLACEHOLDER_STYLE = {
  minHeight: 120,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  color: "#666",
  backgroundColor: "#f8f9fa",
  border: "1px dashed #dee2e6",
  borderRadius: 4,
  padding: 12,
  textAlign: "center",
};

/** Min height for the Bokeh root while embedding; avoids zero-size layout. */
const BOKEH_EMBED_MIN_HEIGHT_PX = 280;

/**
 * Bokeh measures the target element during embed_item. If the plot div uses
 * display:none, layout width/height are zero and the canvas often stays blank.
 * Keep the target in normal flow and cover it with a placeholder overlay until ready.
 */
const PLACEHOLDER_OVERLAY_STYLE = {
  ...PLACEHOLDER_STYLE,
  position: "absolute",
  inset: 0,
  zIndex: 1,
  minHeight: "100%",
  boxSizing: "border-box",
};

/**
 * Injects Bokeh plot from API.
 * Accepts only Bokeh `json_item` payloads to avoid executing untrusted HTML/JS.
 * Shows "Data not available." in the plot area when there is no data or when the plot fails to load.
 */
export default function BokehEmbed({
  item,
  id = "bokeh-embed",
  plotName,
  unavailableReason,
  onPlotReadyChange,
  fillHeight = false,
  maximizeInContainer = false,
}) {
  const session = useSession();
  const canViewErrorDetails = !!session?.is_staff;
  const containerRef = useRef(null);
  const [plotReady, setPlotReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [failureReason, setFailureReason] = useState(null);
  const [showDetails, setShowDetails] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");

  const hasData = !!item;
  const showPlaceholder = !hasData || !plotReady || loadFailed;

  useEffect(() => {
    setPlotReady(false);
    setLoadFailed(false);
    setFailureReason(null);
    setShowDetails(false);
    setCopyStatus("");
    if (onPlotReadyChange) onPlotReadyChange(false);
  }, [item, id, onPlotReadyChange]);

  useEffect(() => {
    if (!item) return;

    let cancelled = false;
    whenBokehReady()
      .then(() => {
        if (cancelled || !containerRef.current) return;
        const el = document.getElementById(id);
        if (!el || !window.Bokeh?.embed?.embed_item) {
          if (!cancelled) {
            setFailureReason("Bokeh embed target or embed_item not available");
            setLoadFailed(true);
          }
          return;
        }
        try {
          const embedResult = window.Bokeh.embed.embed_item(item, id);
          Promise.resolve(embedResult)
            .then(() => {
              if (cancelled) return;
              if (maximizeInContainer) maximizeEmbeddedPlot(id);
              setPlotReady(true);
              if (onPlotReadyChange) onPlotReadyChange(true);
            })
            .catch((err) => {
              if (cancelled) return;
              setFailureReason(err?.message || "Embed failed");
              setLoadFailed(true);
              if (onPlotReadyChange) onPlotReadyChange(false);
            });
        } catch (err) {
          console.warn("Bokeh embed_item failed:", err);
          if (!cancelled) {
            setFailureReason(err?.message || "Embed failed");
            setLoadFailed(true);
            if (onPlotReadyChange) onPlotReadyChange(false);
          }
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setFailureReason(err?.message || "Bokeh JS did not load in time");
          setLoadFailed(true);
          if (onPlotReadyChange) onPlotReadyChange(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [item, id, onPlotReadyChange, maximizeInContainer]);

  const detailsMessage = loadFailed ? failureReason : unavailableReason;
  const isLoading = hasData && !plotReady && !loadFailed;
  const isUnavailable = !isLoading && showPlaceholder;
  let message;
  if (isLoading) {
    // Plot is present but still being rendered; show a per-plot loading message.
    message = plotName ? `Loading ${plotName}…` : "Loading plot…";
  } else {
    message = "Data not available.";
  }
  const handleCopyDetails = async () => {
    if (!detailsMessage) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(detailsMessage);
        setCopyStatus("Copied");
      } else {
        setCopyStatus("Clipboard unavailable");
      }
    } catch {
      setCopyStatus("Copy failed");
    }
  };
  const renderPlaceholder = (style) => (
    <div className="bokeh-plot-unavailable" style={style} aria-live="polite">
      <span>{message}</span>
      {isUnavailable && detailsMessage && canViewErrorDetails ? (
        <span
          style={{ marginTop: 8, display: "inline-flex", alignItems: "center", gap: 8, position: "relative" }}
          onMouseEnter={() => setShowDetails(true)}
          onMouseLeave={() => setShowDetails(false)}
        >
          <span style={{ textDecoration: "underline", cursor: "help" }} aria-label="Show plot error details">
            Error Detail
          </span>
          {showDetails ? (
            <span
              style={{
                position: "absolute",
                top: "120%",
                left: 0,
                minWidth: 280,
                maxWidth: 520,
                textAlign: "left",
                backgroundColor: "#fff",
                color: "#111",
                border: "1px solid #ced4da",
                borderRadius: 4,
                padding: 10,
                boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                zIndex: 10,
              }}
              role="tooltip"
            >
              <div style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                {detailsMessage}
              </div>
            </span>
          ) : null}
        </span>
      ) : null}
      {isUnavailable && detailsMessage && canViewErrorDetails ? (
        <span style={{ marginTop: 8, display: "inline-flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={handleCopyDetails}
            style={{ fontSize: "0.4375rem", padding: "0.125rem 0.25rem", lineHeight: 1.1 }}
          >
            Copy Error Detail
          </button>
          {copyStatus ? (
            <span style={{ fontSize: "0.85em" }} aria-live="polite">
              {copyStatus}
            </span>
          ) : null}
        </span>
      ) : null}
    </div>
  );

  const placeholder = renderPlaceholder(PLACEHOLDER_STYLE);
  const placeholderOverlay = renderPlaceholder(PLACEHOLDER_OVERLAY_STYLE);

  const plotTargetLayoutStyle = hasData
    ? {
        display: "block",
        width: "100%",
        height: fillHeight ? "100%" : undefined,
        minHeight: showPlaceholder ? BOKEH_EMBED_MIN_HEIGHT_PX : undefined,
      }
    : {};

  if (item) {
    const overlayActive = hasData && showPlaceholder;
    return (
      <div
        ref={containerRef}
        className="bokeh-embed-wrapper"
        style={{
          position: "relative",
          height: fillHeight ? "100%" : undefined,
          minHeight: overlayActive ? BOKEH_EMBED_MIN_HEIGHT_PX : undefined,
        }}
      >
        {overlayActive ? placeholderOverlay : null}
        <div id={id} className="bokeh-embed" style={plotTargetLayoutStyle} />
      </div>
    );
  }

  return (
    <div ref={containerRef} className="bokeh-embed-wrapper">
      {placeholder}
    </div>
  );
}
