import { useEffect, useRef } from "react";

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
 * Injects Bokeh script and div from API (dangerouslySetInnerHTML for div, script execution for script).
 * Waits for Bokeh JS to be loaded before running the embed script so plots render correctly.
 */
export default function BokehEmbed({ script, div, id = "bokeh-embed" }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!script || !containerRef.current) return;
    const wrap = containerRef.current.querySelector(".bokeh-script-wrap");
    if (!wrap) return;

    let cancelled = false;
    whenBokehReady()
      .then(() => {
        if (cancelled || !containerRef.current) return;
        const prev = wrap.querySelector("script");
        if (prev) prev.remove();
        const el = document.createElement("script");
        el.type = "text/javascript";
        el.textContent = script;
        wrap.appendChild(el);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [script]);

  if (!div && !script) return null;

  return (
    <div ref={containerRef}>
      <div className="bokeh-script-wrap" style={{ display: "none" }} />
      {div && (
        <div
          id={id}
          className="bokeh-embed"
          dangerouslySetInnerHTML={{ __html: div }}
        />
      )}
    </div>
  );
}
