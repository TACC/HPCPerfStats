import BokehEmbed from "./BokehEmbed";
import type { BokehPlotWithLimitationProps } from "@/types/bokeh";

/** Visually-hidden prose for screen readers; chart data is not exposed from Bokeh canvas. */
export const BOKEH_PLOT_LIMITATION_TEXT =
  "Interactive performance chart. Scales to the available width. Numerical detail may not be read by assistive technology.";

/**
 * Bokeh embed with a hidden limitation description wired via aria-describedby.
 * Canonical pattern for user-facing plots outside Job Detail PlotPanel.
 */
export default function BokehPlotWithLimitation({
  id,
  plotDescId: plotDescIdProp,
  wrapperClassName,
  hostClassName,
  ...bokehProps
}: BokehPlotWithLimitationProps) {
  const plotDescId = plotDescIdProp ?? `${id}-plot-desc`;
  return (
    <div className={hostClassName}>
      <p id={plotDescId} className="sr-only">
        {BOKEH_PLOT_LIMITATION_TEXT}
      </p>
      <BokehEmbed
        id={id}
        wrapperClassName={wrapperClassName}
        ariaDescribedBy={plotDescId}
        {...bokehProps}
      />
    </div>
  );
}
