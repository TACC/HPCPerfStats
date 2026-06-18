"""Small Bokeh figure helpers for SPA-embedded machine pages."""

from bokeh.plotting import figure

from hpcperfstats.analysis.metrics.lib.gen.utils import set_linear_axes_plain_numeric


def new_spa_embedded_figure(*, width, height, title=None, **kwargs):
    """Return an embed-safe Bokeh figure with toolbar/tools disabled."""
    plot = figure(
        width=width,
        height=height,
        title=title,
        toolbar_location=None,
        tools=[],
        output_backend="canvas",
        **kwargs,
    )
    set_linear_axes_plain_numeric(plot)
    return plot
