import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { BokehEmbedProps } from "@/types/bokeh";
import { useSession } from "../session-context";
import {
  DEFAULT_INTERSECTION_ROOT_MARGIN,
  DEFAULT_INTERSECTION_THRESHOLD,
  BOKEH_EMBED_LOCK_SHARDS,
  defaultDeferEmbedUntilVisible,
  defaultEmbedSettleAfterIdleMs,
  bokehEmbedLockShard,
  delayMs,
  isVitestLike,
} from "../utils/bokeh-embed-defaults";
import { prepareBokehJsonItemForEmbed } from "../utils/remap-bokeh-json-item-ids";
import { parseBokehJsonItem } from "@/schemas/api/bokeh-json-item-schema";
import { waitForBokehEmbedDocumentIdle } from "../utils/bokeh-when-document-idle";
import { ensureBokehLoaded } from "../bokehInit";

type WhenBokehReadyOptions = { signal?: AbortSignal };
type LayoutWaitOptions = { timeoutMs?: number; signal?: AbortSignal };
type LayoutWaitResult = { ok: boolean; reason?: "abort" | "timeout" | "no-el" };
type BokehIndexedView = {
  el?: Element;
  model?: {
    sizing_mode?: string;
    width_policy?: string;
    height_policy?: string;
  };
  remove?: () => void;
  request_render?: () => void;
};

/**
 * Poll until window.Bokeh is defined (Bokeh JS loaded), then resolve.
 * Pass `signal` (e.g. AbortController.signal) so unmount clears the interval and rejects with AbortError.
 */
function whenBokehReady(timeoutMs = 10000, options: WhenBokehReadyOptions = {}) {
  const { signal } = options;
  if (typeof window !== "undefined" && window.Bokeh) {
    return Promise.resolve();
  }
  return new Promise<void>((resolve, reject) => {
    let settled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;
    const finish = (fn: () => void) => {
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
        if (intervalId !== null) {
          clearInterval(intervalId);
          intervalId = null;
        }
        finish(() => resolve());
        return;
      }
      if (Date.now() > deadline) {
        if (intervalId !== null) {
          clearInterval(intervalId);
          intervalId = null;
        }
        finish(() => reject(new Error("Bokeh JS did not load in time")));
      }
    }, 50);
  });
}

/**
 * Run one Bokeh embed pipeline after any in-flight pipeline finishes.
 *
 * BokehJS can throw when layout runs too early—for example ``CanvasPanelView``
 * reading ``bbox.is_valid`` before ``bbox`` exists, or Bokeh 3.9 ``AxisView``
 * reading ``range.is_valid`` when ``ranges`` are not wired yet after a forced
 * ``resize``. Mitigations: serialize ``embed_item`` across targets, wait for a
 * non-zero layout box before embed, wait for ``Document`` idle after
 * ``embed_item`` resolves, and defer synthetic ``resize`` (see
 * ``maximizeEmbeddedPlot``).
 */
let bokehEmbedChains: Promise<void>[] = Array.from(
  { length: BOKEH_EMBED_LOCK_SHARDS },
  () => Promise.resolve(),
);

function withBokehEmbedLock(embedId: string, run: () => void | Promise<void>) {
  const shard = bokehEmbedLockShard(embedId);
  const next = bokehEmbedChains[shard].then(
    () => Promise.resolve().then(run),
    () => Promise.resolve().then(run),
  );
  bokehEmbedChains[shard] = next.then(
    () => undefined,
    () => undefined,
  );
  return next;
}

/** Bokeh 3.9 sometimes leaves categorical/linear figures unpainted until a resize/layout pass. */
function scheduleBokehLayoutReflow() {
  if (typeof window === "undefined") return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      window.dispatchEvent(new Event("resize"));
      window.setTimeout(() => window.dispatchEvent(new Event("resize")), 72);
    });
  });
}

function isEmbedTargetRenderable(el: HTMLElement | null) {
  if (!el?.isConnected) return false;
  if (isVitestLike()) {
    return true;
  }
  return el.offsetWidth > 0 && el.offsetHeight > 0;
}

function disposeBokehViewsForTarget(targetEl: HTMLElement | null) {
  if (!targetEl || !window.Bokeh?.index) return;
  Object.values(window.Bokeh.index as Record<string, BokehIndexedView>).forEach((view) => {
    try {
      if (!view?.el || !targetEl.contains(view.el)) return;
      if (typeof view.remove === "function") view.remove();
    } catch {
      // Best-effort teardown for stale embedded roots.
    }
  });
}

/**
 * Bokeh measures the embed target's box; if it or an ancestor is not laid out
 * (e.g. HTML `hidden` / `display:none`), width and height are 0 and embed_item
 * can throw inside CanvasPanelView (`bbox` undefined → `is_valid` access).
 *
 * @returns {Promise<{ ok: boolean, reason?: "abort"|"timeout"|"no-el" }>}
 */
function waitForNonZeroLayout(el: HTMLElement | null, options: LayoutWaitOptions = {}) {
  const { timeoutMs = 15000, signal } = options;
  if (!el) {
    return Promise.resolve({ ok: false, reason: "no-el" } satisfies LayoutWaitResult);
  }
  if (isVitestLike()) {
    return Promise.resolve({ ok: true } satisfies LayoutWaitResult);
  }

  const hasSize = () => el.offsetWidth > 0 && el.offsetHeight > 0;
  if (hasSize()) {
    return Promise.resolve({ ok: true } satisfies LayoutWaitResult);
  }

  return new Promise<LayoutWaitResult>((resolve) => {
    let settled = false;
    const finish = (out: LayoutWaitResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(tid);
      ro.disconnect();
      if (signal) {
        signal.removeEventListener("abort", onAbort);
      }
      resolve(out);
    };

    const onAbort = () => finish({ ok: false, reason: "abort" });
    if (signal) {
      if (signal.aborted) {
        finish({ ok: false, reason: "abort" });
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }

    const ro = new ResizeObserver(() => {
      if (hasSize()) finish({ ok: true });
    });
    ro.observe(el);

    const tid = setTimeout(() => finish({ ok: false, reason: "timeout" }), timeoutMs);
  });
}

/**
 * @param {string} targetId
 * @param {"stretch_both" | "stretch_width"} mode stretch_width: full width, intrinsic height (scrollable zoom)
 */
function maximizeEmbeddedPlot(targetId: string, mode: "stretch_both" | "stretch_width" = "stretch_both") {
  const targetEl =
    typeof document !== "undefined" ? document.getElementById(targetId) : null;
  if (!targetEl) return;

  const widthOnly = mode === "stretch_width";

  const forceFillBokehDom = () => {
    const els = targetEl.querySelectorAll(
      ".bk-root, .bk, .bk-layout-box, .bk-plot-layout, .bk-canvas-events, canvas",
    );
    els.forEach((node) => {
      const el = node as HTMLElement;
      el.style.setProperty("width", "100%", "important");
      if (widthOnly) {
        el.style.removeProperty("height");
      } else {
        el.style.setProperty("height", "100%", "important");
      }
      el.style.setProperty("max-width", "none", "important");
    });
  };

  const rootEl = targetEl.querySelector(".bk-root") as HTMLElement | null;
  if (rootEl) {
    rootEl.style.width = "100%";
    if (widthOnly) {
      rootEl.style.height = "auto";
    } else {
      rootEl.style.height = "100%";
    }
    rootEl.style.maxWidth = "none";
  }

  try {
    const index = (window.Bokeh?.index || {}) as Record<string, BokehIndexedView>;
    Object.values(index).forEach((view) => {
      if (!view?.el || !targetEl.contains(view.el) || !view?.model) return;
      view.model.sizing_mode = widthOnly ? "stretch_width" : "stretch_both";
      view.model.width_policy = "max";
      view.model.height_policy = widthOnly ? "auto" : "max";
      if (typeof view.request_render === "function") view.request_render();
    });
  } catch {
    // Best-effort sizing only.
  }

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

  if (typeof window !== "undefined") {
    // Defer past layout: an immediate resize can run Bokeh measure before nested
    // Figure/Axis views finish building (Bokeh 3.9 → `is_valid` on undefined ranges).
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        window.dispatchEvent(new Event("resize"));
      });
    });
  }
}

const PLACEHOLDER_CLASS =
  "bokeh-plot-unavailable flex min-h-[120px] flex-col items-center justify-center rounded border border-dashed border-border bg-muted p-3 text-center text-muted-foreground";

const PLACEHOLDER_OVERLAY_CLASS = cn(
  PLACEHOLDER_CLASS,
  "absolute inset-0 z-[1] box-border min-h-full",
);

/** Min height for the Bokeh root while embedding; avoids zero-size layout. */
const BOKEH_EMBED_MIN_HEIGHT_PX = 280;

/**
 * Bokeh measures the target element during embed_item. If the plot div uses
 * display:none, layout width/height are zero and the canvas often stays blank.
 * Keep the target in normal flow and cover it with a placeholder overlay until ready.
 */

/**
 * Injects Bokeh plot from API.
 * Accepts only Bokeh `json_item` payloads to avoid executing untrusted HTML/JS.
 * Shows an explicit unavailable status plus "Data not available." when there is no data or
 * when the plot fails to load (text status, not color alone).
 * @param {boolean | "width"} maximizeInContainer true = fill container; "width" = stretch width, natural height (zoom scroll)
 * @param {number} [embedMinHeightPx=280] Min height for the plot slot while loading; use the thumbnail box height for job-list thumbs (e.g. 200) so Bokeh measures a box that matches the visible clip.
 * @param {boolean} [deferEmbedUntilVisible] When true (default in production), wait for the wrapper to intersect the viewport before starting embed. Vitest omits this unless explicitly set to true (for IO mocks).
 * @param {string} [intersectionRootMargin] Passed to IntersectionObserver (default `100px 0px`).
 * @param {number} [intersectionThreshold] Passed to IntersectionObserver (default 0.01).
 * @param {number} [embedSettleAfterIdleMs] After Bokeh document idle, delay this many ms before releasing the global embed lock (default 24 in production, 0 in Vitest).
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
  embedMinHeightPx = BOKEH_EMBED_MIN_HEIGHT_PX,
  deferEmbedUntilVisible,
  intersectionRootMargin = DEFAULT_INTERSECTION_ROOT_MARGIN,
  intersectionThreshold = DEFAULT_INTERSECTION_THRESHOLD,
  embedSettleAfterIdleMs,
  embedAllowed = true,
}: BokehEmbedProps) {
  const session = useSession();
  const canViewErrorDetails = !!session?.is_staff;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const userSetDefer = deferEmbedUntilVisible !== undefined;
  const effectiveDefer =
    deferEmbedUntilVisible !== undefined ? deferEmbedUntilVisible : defaultDeferEmbedUntilVisible();
  const useViewportGate = effectiveDefer && !(isVitestLike() && !userSetDefer);
  const effectiveSettleMs =
    embedSettleAfterIdleMs !== undefined
      ? embedSettleAfterIdleMs
      : defaultEmbedSettleAfterIdleMs();
  const [viewportAllowsEmbed, setViewportAllowsEmbed] = useState(false);
  const [plotReady, setPlotReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [failureReason, setFailureReason] = useState<string | null>(null);
  const [errorDetailsOpen, setErrorDetailsOpen] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const errorDetailsPanelId = `${id}-plot-error-details`;
  const failEmbed = (reason: string) => {
    setFailureReason(reason);
    setLoadFailed(true);
    if (onPlotReadyChange) onPlotReadyChange(false);
  };

  const hasData = !!item;
  const showPlaceholder = !hasData || !plotReady || loadFailed;

  const maximizeMode =
    maximizeInContainer === "width"
      ? "stretch_width"
      : maximizeInContainer === true
        ? "stretch_both"
        : null;

  useEffect(() => {
    setPlotReady(false);
    setLoadFailed(false);
    setFailureReason(null);
    setErrorDetailsOpen(false);
    setCopyStatus("");
    if (onPlotReadyChange) onPlotReadyChange(false);
  }, [item, id, onPlotReadyChange]);

  useLayoutEffect(() => {
    if (!item) {
      setViewportAllowsEmbed(false);
      return;
    }
    if (!useViewportGate) {
      setViewportAllowsEmbed(true);
      return;
    }
    setViewportAllowsEmbed(false);
    const el = containerRef.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setViewportAllowsEmbed(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry?.isIntersecting) {
          setViewportAllowsEmbed(true);
        }
      },
      { root: null, rootMargin: intersectionRootMargin, threshold: intersectionThreshold },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [item, id, useViewportGate, intersectionRootMargin, intersectionThreshold]);

  useEffect(() => {
    if (!item || !viewportAllowsEmbed || !embedAllowed) return;
    if (!parseBokehJsonItem(item)) {
      failEmbed("Plot payload was invalid; data is unavailable.");
      return;
    }

    let cancelled = false;
    const bokehWait = new AbortController();
    withBokehEmbedLock(id, () => {
      if (cancelled) return;
      return ensureBokehLoaded()
        .then(() => whenBokehReady(10000, { signal: bokehWait.signal }))
        .then(() => {
          if (cancelled || !containerRef.current) return;
          const el = document.getElementById(id);
          if (!el || !window.Bokeh?.embed?.embed_item) {
            if (!cancelled) failEmbed("Bokeh embed target or embed_item not available");
            return;
          }
          return waitForNonZeroLayout(el, {
            timeoutMs: 15000,
            signal: bokehWait.signal,
          }).then((layout) => {
            if (cancelled || !containerRef.current) return;
            if (!layout.ok) {
              if (layout.reason === "abort") return;
              failEmbed(
                layout.reason === "timeout"
                  ? "Chart container stayed at zero size (try showing the charts panel)."
                  : "Chart embed target is missing from the page.",
              );
              return;
            }
            try {
              // Re-embedding into the same target (e.g., normal -> zoom item swap)
              // can otherwise leave duplicate Bokeh roots in the container.
              if (!isEmbedTargetRenderable(el)) {
                failEmbed("Chart container is detached or hidden before embed.");
                return;
              }
              disposeBokehViewsForTarget(el);
              el.innerHTML = "";
              const embedPayload = prepareBokehJsonItemForEmbed(item);
              const embedResult = window.Bokeh.embed.embed_item(embedPayload, id);
              return Promise.resolve(embedResult)
                .then((views) => waitForBokehEmbedDocumentIdle(views))
                .then(() => delayMs(effectiveSettleMs))
                .then(() => {
                  if (cancelled) return;
                  if (!isEmbedTargetRenderable(el)) {
                    failEmbed("Chart container changed before render completed.");
                    return;
                  }
                  function markPlotReady() {
                    if (cancelled) return;
                    if (!isEmbedTargetRenderable(el)) {
                      failEmbed("Chart container changed before render completed.");
                      return;
                    }
                    scheduleBokehLayoutReflow();
                    if (maximizeMode) maximizeEmbeddedPlot(id, maximizeMode);
                    setPlotReady(true);
                    if (onPlotReadyChange) onPlotReadyChange(true);
                  }
                  if (typeof requestAnimationFrame === "function") {
                    requestAnimationFrame(() => requestAnimationFrame(markPlotReady));
                  } else {
                    markPlotReady();
                  }
                })
                .catch((err: unknown) => {
                  if (cancelled) return;
                  const message =
                    err instanceof Error ? err.message : "Embed failed";
                  failEmbed(message);
                });
            } catch (err) {
              console.warn("Bokeh embed_item failed:", err);
              if (!cancelled) {
                const message =
                  err instanceof Error ? err.message : "Embed failed";
                failEmbed(message);
              }
            }
          });
        })
        .catch((err) => {
          if (cancelled || err?.name === "AbortError") return;
          failEmbed(err?.message || "Bokeh JS did not load in time");
        });
    });

    return () => {
      cancelled = true;
      bokehWait.abort();
      const el = typeof document !== "undefined" ? document.getElementById(id) : null;
      disposeBokehViewsForTarget(el);
    };
  }, [item, id, onPlotReadyChange, maximizeMode, viewportAllowsEmbed, effectiveSettleMs, embedAllowed]);

  useEffect(() => {
    if (embedAllowed) return;
    setPlotReady(false);
    setLoadFailed(false);
    setFailureReason(null);
    setErrorDetailsOpen(false);
    if (onPlotReadyChange) onPlotReadyChange(false);
  }, [embedAllowed, onPlotReadyChange]);

  useEffect(() => {
    if (!plotReady || !maximizeMode) return;
    function onResize() {
      const el = typeof document !== "undefined" ? document.getElementById(id) : null;
      if (!isEmbedTargetRenderable(el) || !maximizeMode) return;
      maximizeEmbeddedPlot(id, maximizeMode);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [plotReady, maximizeMode, id]);

  useEffect(() => {
    if (!errorDetailsOpen) return;
    function onKeyDown(e: KeyboardEvent) {
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
    message = plotName ? `Loading ${plotName}…` : "Loading plot…";
  } else if (loadFailed && failureReason) {
    message = failureReason;
  } else if (isUnavailable && unavailableReason) {
    message = unavailableReason;
  } else if (isUnavailable) {
    message = "Unavailable — Data not available.";
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
  const renderPlaceholder = (className: string) => (
    <div
      className={className}
      aria-live="polite"
      role="status"
    >
      <span>{message}</span>
      {isUnavailable && detailsMessage && canViewErrorDetails && detailsMessage !== message ? (
        <div className="bokeh-plot-error-detail-controls mt-2 flex w-full flex-col items-center gap-2">
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto p-0"
            aria-expanded={errorDetailsOpen}
            aria-controls={errorDetailsPanelId}
            onClick={() => setErrorDetailsOpen((o) => !o)}
          >
            {errorDetailsOpen ? "Hide plot error details" : "Show plot error details"}
          </Button>
          {errorDetailsOpen ? (
            <div
              id={errorDetailsPanelId}
              role="region"
              aria-label="Plot error details"
              className={cn(
                "bokeh-plot-error-detail-panel w-full max-w-[520px] rounded-md border border-border bg-background p-2 text-left text-sm text-foreground",
              )}
              style={{
                whiteSpace: "normal",
                wordBreak: "break-word",
              }}
            >
              {detailsMessage}
            </div>
          ) : null}
          <div className="inline-flex flex-wrap items-center justify-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={handleCopyDetails}>
              Copy error detail
            </Button>
            {copyStatus ? (
              <span className="text-sm" aria-live="polite">
                {copyStatus}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );

  const placeholder = renderPlaceholder(PLACEHOLDER_CLASS);
  const placeholderOverlay = renderPlaceholder(PLACEHOLDER_OVERLAY_CLASS);

  const plotTargetLayoutStyle = hasData
    ? {
        display: "block",
        width: "100%",
        height: fillHeight ? "100%" : undefined,
        minHeight: showPlaceholder ? embedMinHeightPx : undefined,
      }
    : {};

  const wrapperClass = cn(
    "bokeh-embed-wrapper max-md:max-w-full max-md:overflow-x-auto max-md:[-webkit-overflow-scrolling:touch]",
    wrapperClassName && String(wrapperClassName).trim(),
  );

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
          minHeight: overlayActive ? embedMinHeightPx : undefined,
        }}
      >
        {overlayActive ? placeholderOverlay : null}
        <div
          id={id}
          className="bokeh-embed max-md:max-w-full max-md:[&_.bk-root]:max-w-full! max-md:[&_canvas]:max-w-full! max-md:[&_svg]:max-w-full!"
          style={plotTargetLayoutStyle}
        />
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
