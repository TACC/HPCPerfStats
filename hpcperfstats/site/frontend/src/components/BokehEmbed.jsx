import { useEffect, useRef, useState } from "react";
import { useSession } from "../session-context";

/**
 * Poll until window.Bokeh is defined (Bokeh JS loaded), then resolve.
 * Pass `signal` (e.g. AbortController.signal) so unmount clears the interval and rejects with AbortError.
 */
function whenBokehReady(timeoutMs = 10000, options = {}) {
  const signal = options.signal;
  if (typeof window !== "undefined" && window.Bokeh) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    let intervalId = null;
    const finish = (fn) => {
      if (settled) return;
      settled = true;
      fn();
    };

    const onAbort = () => {
      if (intervalId !== null) {
        clearInterval(intervalId);
        intervalId = null;
      }
      finish(() => reject(new DOMException("Bokeh wait aborted", "AbortError")));
    };

    if (signal) {
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }

    const deadline = Date.now() + timeoutMs;
    intervalId = setInterval(() => {
      if (typeof window !== "undefined" && window.Bokeh) {
        clearInterval(intervalId);
        intervalId = null;
        finish(() => resolve());
        return;
      }
      if (Date.now() > deadline) {
        clearInterval(intervalId);
        intervalId = null;
        finish(() => reject(new Error("Bokeh JS did not load in time")));
      }
    }, 50);
  });
}

function maximizeEmbeddedPlot(targetId) {
  const targetEl = typeof document !== "undefined" ? document.getElementById(targetId) : null;
  if (!targetEl) return;

  const forceFillBokehDom = () => {
    const els = targetEl.querySelectorAll(
      ".bk-root, .bk, .bk-layout-box, .bk-plot-layout, .bk-canvas-events, canvas"
    );
    els.forEach((el) => {
      el.style.setProperty("width", "100%", "important");
      el.style.setProperty("height", "100%", "important");
      el.style.setProperty("max-width", "none", "important");
    });
  };

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

  // Bokeh may apply fixed inline sizes after the initial embed pass.
  // Re-apply fill sizing over a few ticks so the zoom plot truly expands.
  let attempts = 0;
  const maxAttempts = 8;
  const repaint = () => {
    forceFillBokehDom();
    attempts += 1;
    if (attempts < maxAttempts) {
      window.setTimeout(repaint, 40);
    }
  };
  forceFillBokehDom();
  window.setTimeout(repaint, 0);

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
 * Shows an explicit unavailable status plus "Data not available." when there is no data or
 * when the plot fails to load (text status, not color alone).
 */
export default function BokehEmbed({
  item,
  id = "bokeh-embed",
  plotName,
  unavailableReason,
  onPlotReadyChange,
  fillHeight = false,
  maximizeInContainer = false,
  isLoadingExternal = false,
  wrapperClassName = "",
  embedAriaLabel,
  ariaDescribedBy,
}) {
  const session = useSession();
  const canViewErrorDetails = !!session?.is_staff;
  const containerRef = useRef(null);
  const [plotReady, setPlotReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [failureReason, setFailureReason] = useState(null);
  const [errorDetailsOpen, setErrorDetailsOpen] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const errorDetailsPanelId = `${id}-plot-error-details`;

  const hasData = !!item;
  const showPlaceholder = !hasData || !plotReady || loadFailed;

  useEffect(() => {
    setPlotReady(false);
    setLoadFailed(false);
    setFailureReason(null);
    setErrorDetailsOpen(false);
    setCopyStatus("");
    if (onPlotReadyChange) onPlotReadyChange(false);
  }, [item, id, onPlotReadyChange]);

  useEffect(() => {
    if (!item) return;

    let cancelled = false;
    const bokehWait = new AbortController();
    whenBokehReady(10000, { signal: bokehWait.signal })
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
          // Re-embedding into the same target (e.g., normal -> zoom item swap)
          // can otherwise leave duplicate Bokeh roots in the container.
          el.innerHTML = "";
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
        if (cancelled || err?.name === "AbortError") return;
        setFailureReason(err?.message || "Bokeh JS did not load in time");
        setLoadFailed(true);
        if (onPlotReadyChange) onPlotReadyChange(false);
      });

    return () => {
      cancelled = true;
      bokehWait.abort();
    };
  }, [item, id, onPlotReadyChange, maximizeInContainer]);

  useEffect(() => {
    if (!errorDetailsOpen) return;
    function onKeyDown(e) {
      if (e.key === "Escape") {
        setErrorDetailsOpen(false);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [errorDetailsOpen]);

  const detailsMessage = loadFailed ? failureReason : unavailableReason;
  const isLoading = !!isLoadingExternal || (hasData && !plotReady && !loadFailed);
  const isUnavailable = !isLoading && showPlaceholder;
  let message;
  if (isLoading) {
    // Plot is present but still being rendered; show a per-plot loading message.
    message = plotName ? `Loading ${plotName}…` : "Loading plot…";
  } else {
    message = "Unavailable — Data not available.";
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
    <div
      className="bokeh-plot-unavailable"
      style={style}
      aria-live="polite"
      role="status"
    >
      <span>{message}</span>
      {isUnavailable && detailsMessage && canViewErrorDetails ? (
        <div className="bokeh-plot-error-detail-controls mt-2 d-flex flex-column align-items-center gap-2 w-100">
          <button
            type="button"
            className="btn btn-link btn-sm p-0"
            aria-expanded={errorDetailsOpen}
            aria-controls={errorDetailsPanelId}
            onClick={() => setErrorDetailsOpen((o) => !o)}
          >
            {errorDetailsOpen ? "Hide plot error details" : "Show plot error details"}
          </button>
          {errorDetailsOpen ? (
            <div
              id={errorDetailsPanelId}
              role="region"
              aria-label="Plot error details"
              className="bokeh-plot-error-detail-panel w-100 text-start border rounded p-2 small"
              style={{
                maxWidth: 520,
                backgroundColor: "#fff",
                color: "#111",
                borderColor: "#ced4da",
                whiteSpace: "normal",
                wordBreak: "break-word",
              }}
            >
              {detailsMessage}
            </div>
          ) : null}
          <div className="d-inline-flex align-items-center gap-2 flex-wrap justify-content-center">
            <button
              type="button"
              className="btn btn-outline-secondary btn-sm"
              onClick={handleCopyDetails}
            >
              Copy error detail
            </button>
            {copyStatus ? (
              <span className="small" aria-live="polite">
                {copyStatus}
              </span>
            ) : null}
          </div>
        </div>
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

  const wrapperClass =
    wrapperClassName && String(wrapperClassName).trim()
      ? `bokeh-embed-wrapper ${wrapperClassName.trim()}`
      : "bokeh-embed-wrapper";

  const regionLabel =
    embedAriaLabel ??
    (plotName ? `Interactive chart: ${plotName}` : "Interactive chart");
  const describedBy =
    ariaDescribedBy && String(ariaDescribedBy).trim()
      ? String(ariaDescribedBy).trim()
      : undefined;

  if (item) {
    const overlayActive = hasData && showPlaceholder;
    return (
      <div
        ref={containerRef}
        className={wrapperClass}
        role="region"
        aria-label={regionLabel}
        aria-describedby={describedBy}
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
    <div
      ref={containerRef}
      className={wrapperClass}
      role="region"
      aria-label={regionLabel}
      aria-describedby={describedBy}
    >
      {placeholder}
    </div>
  );
}
