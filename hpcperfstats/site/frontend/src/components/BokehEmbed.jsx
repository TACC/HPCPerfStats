import { useEffect, useRef } from "react";

/**
 * Injects Bokeh script and div from API (dangerouslySetInnerHTML for div, script execution for script).
 */
export default function BokehEmbed({ script, div, id = "bokeh-embed" }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!script || !containerRef.current) return;
    const wrap = containerRef.current.querySelector(".bokeh-script-wrap");
    if (!wrap) return;
    const prev = wrap.querySelector("script");
    if (prev) prev.remove();
    const el = document.createElement("script");
    el.type = "text/javascript";
    el.textContent = script;
    wrap.appendChild(el);
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
