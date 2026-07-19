/** Bokeh `json_item` document payload from the Django API (opaque tree; safe for embed_item). */
export type BokehJsonItem = Record<string, unknown>;

export type BokehEmbedMaximizeMode = boolean | "width";

export type BokehEmbedProps = {
  item?: BokehJsonItem | null;
  id?: string;
  plotName?: string;
  unavailableReason?: string | null;
  onPlotReadyChange?: (ready: boolean) => void;
  fillHeight?: boolean;
  maximizeInContainer?: BokehEmbedMaximizeMode;
  isLoadingExternal?: boolean;
  wrapperClassName?: string;
  embedAriaLabel?: string;
  ariaDescribedBy?: string;
  embedMinHeightPx?: number;
  deferEmbedUntilVisible?: boolean;
  intersectionRootMargin?: string;
  intersectionThreshold?: number;
  embedSettleAfterIdleMs?: number;
  /** Stagger concurrent list-surface embeds (index × LIST_EMBED_STAGGER_MS). */
  embedStaggerIndex?: number;
  /** When false, defer Bokeh embed until the plot container is visible in layout. */
  embedAllowed?: boolean;
  /**
   * List/dashboard preview: non-interactive canvas (pointer-events none),
   * no global resize reflow cascade, no continuous maximize-on-resize listener.
   */
  previewMode?: boolean;
};

export type BokehPlotWithLimitationProps = BokehEmbedProps & {
  plotDescId?: string;
  hostClassName?: string;
};
