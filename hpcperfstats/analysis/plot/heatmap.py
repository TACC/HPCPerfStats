"""Heatmap plot: CPI (cycles/instruction) per host per time for a job using utils and Bokeh rects.

"""
import hpcperfstats.conf_parser as cfg

openblas_threads = int(cfg.get_total_cores()) / 4
if openblas_threads < 1:
  openblas_threads = 1

import os

os.environ['OPENBLAS_NUM_THREADS'] = str(openblas_threads)

import numpy
from bokeh.models import (
    BasicTicker,
    ColorBar,
    ColumnDataSource,
    HoverTool,
    LinearColorMapper,
)
from bokeh.palettes import brewer
from bokeh.plotting import figure

from hpcperfstats.analysis.gen import utils


class HeatMap():
  """Builds a Bokeh heatmap of CPI (cycles/instruction) by host and time from a utils-compatible job.

    """

  def plot(self, job):
    """Compute per-host CPI from PMC CLOCKS_UNHALTED_CORE and INSTRUCTIONS_RETIRED, return a Bokeh figure with rect glyphs and color bar.

        """
    u = utils.utils(job)
    schema, _stats = u.get_type("pmc")

    host_cpi = []
    for hostname, stats in _stats.items():
      cpi = numpy.diff(
          stats[:, schema["CLOCKS_UNHALTED_CORE"].index]) / numpy.diff(
              stats[:, schema["INSTRUCTIONS_RETIRED"].index])
      host_cpi += [numpy.append(cpi, cpi[-1])]
    host_cpi = numpy.array(host_cpi).flatten()
    host_cpi = numpy.nan_to_num(host_cpi)
    times = (job.times - job.times[0]).astype(str)
    data = ColumnDataSource(
        dict(hostnames=[h for host in u.hostnames for h in [host] * len(times)],
             times=list(times) * len(u.hostnames),
             cpi=host_cpi))

    hover = HoverTool(tooltips=[("cpi", "@cpi")])

    mapper = LinearColorMapper(palette=brewer["Spectral"][10][::-1],
                               low=0.25,
                               high=2)
    colors = {"field": "cpi", "transform": mapper}
    color_bar = ColorBar(color_mapper=mapper,
                         location=(0, 0),
                         ticker=BasicTicker(desired_num_ticks=10))

    hm = figure(title="<Cycles/Instruction> = " +
                "{0:0.2}".format(host_cpi.mean()),
                x_range=times,
                logo=None,
                y_range=u.hostnames,
                tools=[hover])

    hm.rect("times",
            "hostnames",
            source=data,
            width=1,
            height=1,
            line_color=None,
            fill_color=colors)

    hm.add_layout(color_bar, "right")

    hm.axis.axis_line_color = None
    hm.axis.major_tick_line_color = None
    hm.axis.major_label_text_font_size = "5pt"
    hm.axis.major_label_standoff = 0
    hm.xaxis.major_label_orientation = 1.0

    return hm
