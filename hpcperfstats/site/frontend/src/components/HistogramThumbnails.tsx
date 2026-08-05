import { useState, useRef, useEffect, useLayoutEffect, useCallback, useId, useMemo } from "react";
import { useSearchParams, usePathname } from "next/navigation";
import type { BokehJsonItem } from "@/types/bokeh";
import type { JobListHistogramEntry } from "@/types/view-models";
import { useIsMobile } from "@/hooks/use-media-query";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import BokehPlotWithLimitation from "./BokehPlotWithLimitation";
import LoadingMessage from "./LoadingMessage";
import { filterIdentitySearchParamsKey } from "@/utils/filter-identity-params";

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
  /** When false, Bokeh embed waits until the distributions panel is visible. */
  embedAllowed?: boolean;
};

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
  embedAllowed = true,
}: HistogramThumbnailProps & { embedAllowed?: boolean }) {
  const isMobile = useIsMobile(MOBILE_BREAKPOINT - 0.02);
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const searchParamsKey = searchParams.toString();
  const filterIdentityKey = useMemo(
    () => filterIdentitySearchParamsKey(new URLSearchParams(searchParamsKey)),
    [searchParamsKey],
  );
  const [expanded, setExpanded] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const [popoverLayoutReady, setPopoverLayoutReady] = useState(false);
  const [thumbLayoutReady, setThumbLayoutReady] = useState(false);
  const thumbActivatorRef = useRef<HTMLButtonElement | null>(null);
  const thumbShellRef = useRef<HTMLDivElement | null>(null);
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
    setExpanded(false);
    setHasOpened(false);
    setPopoverLayoutReady(false);
  }, [filterIdentityKey, pathname]);

  useEffect(() => {
    if (embedAllowed) return;
    setExpanded(false);
    setHasOpened(false);
    setPopoverLayoutReady(false);
  }, [embedAllowed]);

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

  useLayoutEffect(() => {
    if (isMobile) {
      setThumbLayoutReady(false);
      return;
    }
    function measure() {
      const node = thumbShellRef.current;
      return !!(node && node.offsetWidth > 0 && node.offsetHeight > 0);
    }
    if (measure()) {
      setThumbLayoutReady(true);
      return;
    }
    setThumbLayoutReady(false);
    let cancelled = false;
    let raf2: number | null = null;
    const raf1 = window.requestAnimationFrame(() => {
      if (cancelled) return;
      if (measure()) {
        setThumbLayoutReady(true);
        return;
      }
      raf2 = window.requestAnimationFrame(() => {
        if (!cancelled) setThumbLayoutReady(true);
      });
    });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf1);
      if (raf2 != null) window.cancelAnimationFrame(raf2);
    };
    // Measure shell size only — do not reset on plotItemThumb identity changes
    // (that unmounts Bokeh to LoadingMessage and causes remount flicker).
  }, [isMobile]);

  /* Mobile: full histogram only, no popover, container sized for viewport.
     Chart title lives inside the Bokeh figure — do not duplicate it outside. */
  if (isMobile) {
    return (
      <div className="flex w-full max-w-full flex-col justify-stretch">
        <div className="max-h-[min(400px,50vh)] min-h-[280px] w-full overflow-auto rounded-[var(--radius)] border border-border bg-background max-md:max-h-[min(400px,50vh)]">
          <BokehPlotWithLimitation
            item={plotItemFull}
            id={fullId}
            plotName={safeTitle}
            unavailableReason={unavailableReason}
            embedAllowed={embedAllowed}
            embedStaggerIndex={index}
            previewMode
            intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
            wrapperClassName="min-h-[280px] w-full"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="histogram-thumbnail-wrapper flex flex-col items-center justify-start">
      <div
        className="histogram-thumbnail-card box-border flex flex-col overflow-hidden rounded-[var(--radius)] border border-border bg-muted"
        style={{ width: THUMB_SIZE.width }}
      >
        <div
          ref={thumbShellRef}
          className="histogram-thumbnail-shell relative box-border flex h-[200px] w-[280px] min-w-0 flex-col items-center overflow-hidden border-0! bg-muted"
        >
          {thumbLayoutReady && embedAllowed ? (
            <BokehPlotWithLimitation
              item={plotItemThumb}
              id={thumbId}
              plotName={safeTitle}
              unavailableReason={unavailableReason}
              embedAllowed={embedAllowed}
              embedStaggerIndex={index}
              embedMinHeightPx={THUMB_SIZE.height}
              previewMode
              intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
              wrapperClassName="h-[200px] w-[280px] min-h-[200px] min-w-0 overflow-hidden [&_.bokeh-embed-wrapper]:h-full [&_.bokeh-embed-wrapper]:w-full [&_.bokeh-embed-wrapper]:max-w-full [&_.bokeh-embed-wrapper]:min-w-0 [&_.bokeh-embed-wrapper]:overflow-hidden [&_.bokeh-embed]:box-border [&_.bokeh-embed]:h-full [&_.bokeh-embed]:min-h-[200px] [&_.bokeh-embed]:w-full [&_.bokeh-embed]:max-w-full [&_.bokeh-embed]:min-w-0 [&_.bokeh-embed]:overflow-hidden [&_.bokeh-embed_.bk-root]:max-w-full!"
            />
          ) : (
            <LoadingMessage message={`Loading ${safeTitle.toLowerCase()}…`} />
          )}
        </div>
        <div className="histogram-thumbnail-actions flex justify-center border-t border-border bg-background px-2 py-[0.35rem]">
          <Button
            ref={thumbActivatorRef}
            type="button"
            variant="outline"
            size="sm"
            aria-label={`${safeTitle}: enlarge chart`}
            aria-expanded={expanded}
            className="histogram-thumbnail-enlarge min-h-[34px]"
            onClick={handleThumbActivate}
          >
            Enlarge chart
          </Button>
        </div>
      </div>
      <Dialog modal={false} open={expanded} onOpenChange={handleDialogOpenChange}>
        <DialogContent
          showCloseButton={false}
          showOverlay={false}
          className="max-w-[calc(100vw-2rem)] sm:max-w-4xl"
          data-testid="histogram-enlarge-dialog"
        >
          <DialogHeader className="flex-row flex-wrap items-center justify-end gap-2 space-y-0">
            {/* Visually hidden: title is already drawn inside the Bokeh figure. */}
            <DialogTitle className="sr-only">{safeTitle}</DialogTitle>
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
            className="histogram-thumbnail-popover-plot relative box-border h-[400px] w-[600px] max-w-full rounded border border-border bg-background [&_.bokeh-embed-wrapper]:min-h-[400px] [&_.bokeh-embed-wrapper]:w-full [&_.bokeh-embed-wrapper]:max-w-full [&_.bokeh-embed-wrapper]:min-w-0"
          >
            {hasOpened && popoverLayoutReady ? (
              <BokehPlotWithLimitation
                item={plotItemFull}
                id={fullId}
                plotName={`${safeTitle} (full)`}
                unavailableReason={unavailableReason}
                embedAllowed={embedAllowed}
                maximizeInContainer="width"
                intersectionRootMargin={HISTOGRAM_INTERSECTION_ROOT_MARGIN}
              />
            ) : (
              <LoadingMessage message={`Loading ${safeTitle.toLowerCase()}…`} />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function HistogramThumbnails({
  histograms,
  embedAllowed = true,
}: HistogramThumbnailsProps) {
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
      className="histogram-thumbnails-grid grid justify-center gap-4 max-md:grid-cols-1 max-md:gap-3"
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
          embedAllowed={embedAllowed}
        />
      ))}
    </section>
  );
}
