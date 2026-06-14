import { useState, useRef, useEffect, useLayoutEffect, useCallback, useId } from "react";
import type { BokehJsonItem } from "@/types/bokeh";
import type { JobListHistogramEntry } from "@/types/view-models";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import BokehPlotWithLimitation from "./BokehPlotWithLimitation";
import LoadingMessage from "./LoadingMessage";

const THUMB_SIZE = { width: 280, height: 200 };
/** Slightly larger prefetch margin than BokehEmbed default so off-screen thumbs start loading sooner. */
const HISTOGRAM_INTERSECTION_ROOT_MARGIN = "120px 0px";
const MOBILE_BREAKPOINT = 768;

type HistogramThumbnailProps = {
  index: number;
  title?: string;
  plotItemThumb?: BokehJsonItem | null;
  plotItemFull?: BokehJsonItem | null;
  unavailableReason?: string | null;
};

type HistogramThumbnailsProps = {
  histograms?: JobListHistogramEntry[] | null;
};

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
function HistogramThumbnail({
  index,
  title,
  plotItemThumb,
  plotItemFull,
  unavailableReason,
}: HistogramThumbnailProps) {
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const [popoverLayoutReady, setPopoverLayoutReady] = useState(false);
  const thumbActivatorRef = useRef<HTMLButtonElement | null>(null);
  const popoverPlotRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const domSuffix = useId().replace(/:/g, "");
  const safeTitle = title || "Histogram";

  const thumbId = `hist-thumb-${index}-${domSuffix}`;
  const fullId = `hist-full-${index}-${domSuffix}`;

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

  const handleDialogOpenChange = (open: boolean) => {
    if (open) {
      setExpanded(true);
      setHasOpened(true);
      return;
    }
    collapseExpanded();
  };

  useEffect(() => {
    if (!expanded || isMobile) return;
    const id = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [expanded, isMobile]);

  useLayoutEffect(() => {
    if (!expanded || isMobile) {
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
    let raf2: number | null = null;
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
  }, [expanded, isMobile]);

  /* Mobile: full histogram only, no popover, container sized for viewport */
  if (isMobile) {
    return (
      <div className="histogram-thumbnail-wrapper histogram-mobile">
        <div className="histogram-mobile-title">{safeTitle}</div>
        <div className="histogram-mobile-plot">
          <BokehPlotWithLimitation
            item={plotItemFull}
            id={fullId}
            plotName={safeTitle}
            unavailableReason={unavailableReason}
            deferEmbedUntilVisible={false}
            intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="histogram-thumbnail-wrapper">
      <div className="histogram-desktop-title">{safeTitle}</div>
      <div
        className="histogram-thumbnail-card"
        style={{ width: THUMB_SIZE.width }}
      >
        <div
          className="histogram-thumbnail histogram-thumbnail-shell bg-muted"
          style={{ height: THUMB_SIZE.height }}
        >
          <BokehPlotWithLimitation
            item={plotItemThumb}
            id={thumbId}
            plotName={safeTitle}
            unavailableReason={unavailableReason}
            embedMinHeightPx={THUMB_SIZE.height}
            maximizeInContainer="width"
            deferEmbedUntilVisible={false}
            wrapperClassName="histogram-thumbnail-bokeh"
            intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
          />
        </div>
        <div className="histogram-thumbnail-actions">
          <Button
            ref={thumbActivatorRef}
            type="button"
            variant="outline"
            size="sm"
            aria-label={`${safeTitle}: enlarge chart`}
            aria-expanded={expanded}
            className="histogram-thumbnail-enlarge"
            onClick={handleThumbActivate}
          >
            Enlarge chart
          </Button>
        </div>
      </div>
      <Dialog open={expanded} onOpenChange={handleDialogOpenChange}>
        <DialogContent
          showCloseButton={false}
          overlayClassName="bg-black/35"
          className="max-w-[calc(100vw-2rem)] sm:max-w-4xl"
        >
          <DialogHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
            <DialogTitle className="min-w-0 flex-1 text-base">{safeTitle}</DialogTitle>
            <Button
              ref={closeButtonRef}
              type="button"
              variant="outline"
              size="sm"
              className="histogram-thumbnail-close shrink-0"
              onClick={collapseExpanded}
              aria-label="Close full size view"
            >
              Close
            </Button>
          </DialogHeader>
          <div
            ref={popoverPlotRef}
            className="histogram-thumbnail-popover-plot h-[400px] rounded border border-border bg-background"
          >
            {hasOpened && popoverLayoutReady ? (
              <BokehPlotWithLimitation
                item={plotItemFull}
                id={fullId}
                plotName={`${safeTitle} (full)`}
                unavailableReason={unavailableReason}
                maximizeInContainer="width"
                deferEmbedUntilVisible={false}
                intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
              />
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function HistogramThumbnails({ histograms }: HistogramThumbnailsProps) {
  if (!histograms) {
    return (
      <div className="flex min-h-[120px] items-center justify-center rounded border border-dashed border-border bg-muted p-3 text-muted-foreground">
        <LoadingMessage message="Loading histograms…" />
      </div>
    );
  }

  if (Array.isArray(histograms) && histograms.length === 0) {
    return null;
  }

  return (
    <section
      className="histogram-thumbnails-grid grid justify-center gap-4"
      style={{
        gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
      }}
      aria-label="Histogram charts for this job list"
    >
      {histograms.map((h: JobListHistogramEntry, i: number) => (
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
