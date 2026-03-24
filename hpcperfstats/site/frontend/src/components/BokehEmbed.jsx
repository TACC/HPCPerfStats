import { useEffect, useRef, useState } from "react";

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

/**
 * Bokeh API returns script wrapped in <script type="text/javascript">...</script>.
 * We must run only the inner JS; putting the full string in a script element's
 * textContent would not execute it.
 */
function extractInlineScript(html) {
  if (typeof html !== "string" || !html.trim()) return html;
  const match = html.trim().match(/^\s*<script[^>]*>([\s\S]*?)<\/script>\s*$/i);
  return match ? match[1].trim() : html;
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
 * - If `item` (Bokeh json_item) is provided: renders a div with `id` and calls
 *   Bokeh.embed.embed_item(item, id). Most reliable for SPAs (e.g. job page).
 * - Otherwise uses `script` + `div` (strip script tag and run inline).
 * Shows "Plot not available" in the plot area when there is no data or when the plot fails to load.
 */
export default function BokehEmbed({ script, div, item, id = "bokeh-embed", plotName, unavailableReason }) {
  const containerRef = useRef(null);
  const [plotReady, setPlotReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [failureReason, setFailureReason] = useState(null);
  const [showDetails, setShowDetails] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");

  const hasData = !!(item || (script && div));
  const showPlaceholder = !hasData || !plotReady || loadFailed;

  useEffect(() => {
    setPlotReady(false);
    setLoadFailed(false);
    setFailureReason(null);
    setShowDetails(false);
    setCopyStatus("");
  }, [item, id, script, div]);

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
          window.Bokeh.embed.embed_item(item, id);
          if (!cancelled) setPlotReady(true);
        } catch (err) {
          console.warn("Bokeh embed_item failed:", err);
          if (!cancelled) {
            setFailureReason(err?.message || "Embed failed");
            setLoadFailed(true);
          }
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setFailureReason(err?.message || "Bokeh JS did not load in time");
          setLoadFailed(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [item, id]);

  useEffect(() => {
    if (item || !script || !containerRef.current) return;
    const wrap = containerRef.current.querySelector(".bokeh-script-wrap");
    if (!wrap) return;

    const scriptToRun = extractInlineScript(script);
    if (!scriptToRun) return;

    let cancelled = false;
    whenBokehReady()
      .then(() => {
        if (cancelled || !containerRef.current) return;
        const prev = wrap.querySelector("script");
        if (prev) prev.remove();
        const el = document.createElement("script");
        el.type = "text/javascript";
        el.textContent = scriptToRun;
        wrap.appendChild(el);
        if (!cancelled) setPlotReady(true);
      })
      .catch((err) => {
        if (!cancelled) {
          setFailureReason(err?.message || "Bokeh JS did not load in time");
          setLoadFailed(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [script, item]);

  const detailsMessage = loadFailed ? failureReason : unavailableReason;
  const isLoading = hasData && !plotReady && !loadFailed;
  const isUnavailable = !isLoading && showPlaceholder;
  let message;
  if (isLoading) {
    // Plot is present but still being rendered; show a per-plot loading message.
    message = plotName ? `Loading ${plotName}…` : "Loading plot…";
  } else {
    message = "Plot not available";
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
      {isUnavailable && detailsMessage ? (
        <span
          style={{ marginTop: 8, display: "inline-flex", alignItems: "center", gap: 8, position: "relative" }}
          onMouseEnter={() => setShowDetails(true)}
          onMouseLeave={() => setShowDetails(false)}
        >
          <span style={{ textDecoration: "underline", cursor: "help" }} aria-label="Show plot error details">
            Details
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
              <div style={{ whiteSpace: "normal", wordBreak: "break-word", marginBottom: 8 }}>
                {detailsMessage}
              </div>
              <button type="button" className="btn btn-outline-secondary btn-sm" onClick={handleCopyDetails}>
                Copy
              </button>
              {copyStatus ? (
                <span style={{ marginLeft: 8, fontSize: "0.85em" }} aria-live="polite">
                  {copyStatus}
                </span>
              ) : null}
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
          minHeight: overlayActive ? BOKEH_EMBED_MIN_HEIGHT_PX : undefined,
        }}
      >
        {overlayActive ? placeholderOverlay : null}
        <div id={id} className="bokeh-embed" style={plotTargetLayoutStyle} />
      </div>
    );
  }

  if (!div && !script) {
    return (
      <div ref={containerRef} className="bokeh-embed-wrapper">
        {placeholder}
      </div>
    );
  }

  const overlayActive = hasData && showPlaceholder;
  return (
    <div
      ref={containerRef}
      className="bokeh-embed-wrapper"
      style={{
        position: "relative",
        minHeight: overlayActive ? BOKEH_EMBED_MIN_HEIGHT_PX : undefined,
      }}
    >
      {overlayActive ? placeholderOverlay : null}
      <div className="bokeh-script-wrap" style={{ display: "none" }} />
      {div && (
        <div
          id={id}
          className="bokeh-embed"
          style={plotTargetLayoutStyle}
          dangerouslySetInnerHTML={{ __html: div }}
        />
      )}
    </div>
  );
}
