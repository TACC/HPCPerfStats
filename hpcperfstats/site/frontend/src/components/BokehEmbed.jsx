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

  const hasData = !!(item || (script && div));
  const showPlaceholder = !hasData || !plotReady || loadFailed;

  useEffect(() => {
    setPlotReady(false);
    setLoadFailed(false);
    setFailureReason(null);
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

  const base = plotName ? `${plotName}: Plot not available` : "Plot not available";
  const reason = loadFailed ? failureReason : unavailableReason;
  const message = reason ? `${base} — ${reason}` : base;
  const placeholder = (
    <div className="bokeh-plot-unavailable" style={PLACEHOLDER_STYLE} aria-live="polite">
      {message}
    </div>
  );

  if (item) {
    return (
      <div ref={containerRef} className="bokeh-embed-wrapper">
        {showPlaceholder ? placeholder : null}
        <div id={id} className="bokeh-embed" style={{ display: showPlaceholder ? "none" : "block" }} />
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

  return (
    <div ref={containerRef} className="bokeh-embed-wrapper">
      {showPlaceholder ? placeholder : null}
      <div className="bokeh-script-wrap" style={{ display: "none" }} />
      {div && (
        <div
          id={id}
          className="bokeh-embed"
          style={{ display: showPlaceholder ? "none" : "block" }}
          dangerouslySetInnerHTML={{ __html: div }}
        />
      )}
    </div>
  );
}
