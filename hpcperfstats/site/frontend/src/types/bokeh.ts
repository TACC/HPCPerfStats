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
  /** When false, defer Bokeh embed until the plot container is visible in layout. */
  embedAllowed?: boolean;
};

export type BokehPlotWithLimitationProps = BokehEmbedProps & {
  plotDescId?: string;
  hostClassName?: string;
};
