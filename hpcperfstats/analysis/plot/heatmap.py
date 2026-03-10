"""Heatmap plot: CPI (cycles/instruction) per host per time for a job using utils and Bokeh rects.

Supports both legacy utils job format and jid_table (ORM) via plot_from_jid_table().
"""
from django.conf import settings
import hpcperfstats.conf_parser as cfg

openblas_threads = getattr(settings, "OPENBLAS_NUM_THREADS", 4)

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
from bokeh.palettes import Viridis
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

    hover = HoverTool(tooltips=[("host", "@hostnames"), ("time", "@times"), ("cpi", "@cpi")])

    # Viridis is colorblind-friendly; scale CPI 0.25–2
    mapper = LinearColorMapper(palette=Viridis[11],
                               low=0.25,
                               high=2)
    colors = {"field": "cpi", "transform": mapper}
    color_bar = ColorBar(color_mapper=mapper,
                         location=(0, 0),
                         ticker=BasicTicker(desired_num_ticks=10))

    hm = figure(
        title="<Cycles/Instruction> = " + "{0:0.2}".format(host_cpi.mean()),
        x_range=times,
        x_axis_label="Time",
        y_axis_label="Host",
        logo=None,
        y_range=u.hostnames,
        tools=[hover],
    )

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


def plot_from_jid_table(jt):
  """Build CPI heatmap from jid_table (ORM). Returns a Bokeh figure or None if no PMC data.

  Uses intel_8pmc3 (APERF, INST_RETIRED) or amd64_pmc (APERF, INST_RETIRED) when available.
  """
  if not jt.host_list:
    return None
  # Try intel then amd PMC for cycles and instructions
  for typ, events_cycles, events_instr in [
      ("intel_8pmc3", ["APERF"], ["INST_RETIRED"]),
      ("amd64_pmc", ["APERF"], ["INST_RETIRED"]),
  ]:
    agg_cyc = jt.get_aggregate_df(typ, "arc", events_cycles, 1.0)
    agg_instr = jt.get_aggregate_df(typ, "arc", events_instr, 1.0)
    if agg_cyc.empty or agg_instr.empty or "sum_val" not in agg_cyc.columns or "sum_val" not in agg_instr.columns:
      continue
    cyc = agg_cyc.rename(columns={"sum_val": "cycles"})[["host", "time", "cycles"]]
    instr = agg_instr.rename(columns={"sum_val": "instr"})[["host", "time", "instr"]]
    merged = cyc.merge(instr, on=["host", "time"], how="inner")
    if merged.empty:
      continue
    merged["cpi"] = merged["cycles"] / merged["instr"].replace(0, numpy.nan)
    merged["cpi"] = merged["cpi"].fillna(0)
    merged["time_str"] = merged["time"].astype(str)
    times = merged["time_str"].unique().tolist()
    hostnames = merged["host"].unique().tolist()
    if not times or not hostnames:
      continue
    cpi_flat = []
    for host in hostnames:
      for t in times:
        row = merged[(merged["host"] == host) & (merged["time_str"] == t)]
        cpi_flat.append(float(row["cpi"].iloc[0]) if len(row) else 0.0)
    source = ColumnDataSource(dict(
        hostnames=[h for h in hostnames for _ in times],
        times=[t for _ in hostnames for t in times],
        cpi=cpi_flat,
    ))
    hover = HoverTool(tooltips=[("host", "@hostnames"), ("time", "@times"), ("cpi", "@cpi")])
    # Viridis is colorblind-friendly; scale CPI 0.25–2
    mapper = LinearColorMapper(palette=Viridis[11], low=0.25, high=2)
    color_bar = ColorBar(color_mapper=mapper, location=(0, 0), ticker=BasicTicker(desired_num_ticks=10))
    mean_cpi = numpy.nanmean(cpi_flat) if cpi_flat else 0
    hm = figure(
        title="<Cycles/Instruction> = {0:0.2f}".format(mean_cpi),
        x_range=times,
        y_range=hostnames,
        x_axis_label="Time",
        y_axis_label="Host",
        tools=[hover],
    )
    hm.rect("times", "hostnames", source=source, width=1, height=1, line_color=None, fill_color={"field": "cpi", "transform": mapper})
    hm.add_layout(color_bar, "right")
    hm.axis.axis_line_color = None
    hm.axis.major_tick_line_color = None
    hm.axis.major_label_text_font_size = "5pt"
    hm.axis.major_label_standoff = 0
    hm.xaxis.major_label_orientation = 1.0
    return hm
  return None
