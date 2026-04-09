"""Heatmap plot: CPI (cycles/instruction) per host per time for a job using utils and Bokeh rects."""

import numpy
from bokeh.models import ColumnDataSource, HoverTool, LinearColorMapper
from bokeh.palettes import Viridis
from bokeh.plotting import figure

from hpcperfstats.analysis.gen import utils
from hpcperfstats.analysis.gen.utils import new_plain_number_hover_formatter
from hpcperfstats.analysis.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.plot.job_window import job_window_label_strings

# Viridis is colorblind-friendly; CPI scale used for both glyph and legend.
_HEATMAP_PALETTE = Viridis[11]
_HEATMAP_CPI_LOW = 0.25
_HEATMAP_CPI_HIGH = 2.0


def _heatmap_figure_title(mean_cpi):
  """Title line including numeric CPI and fixed color-scale bounds (no ColorBar).

  Bokeh ``ColorBar`` + ``json_item`` embed can throw ``metrics_change`` errors in
  the browser; the scale is documented in the title instead.
  """
  return (
      "<Cycles/Instruction> = {:.4f}  ·  CPI colors {:.2f}–{:.2f} (Viridis)"
  ).format(float(mean_cpi), _HEATMAP_CPI_LOW, _HEATMAP_CPI_HIGH)


def _build_heatmap_time_axis_labels(base_times, jt):
  """Ensure categorical heatmap x-axis always includes job start and end labels."""
  labels = list(base_times or [])
  start_label, end_label = job_window_label_strings(jt)
  if start_label is None or end_label is None:
    return labels
  if not labels:
    return [start_label, end_label]
  if labels[0] != start_label:
    labels = [start_label] + labels
  if labels[-1] != end_label:
    labels = labels + [end_label]
  return labels


def _candidate_series():
  """Ordered candidate inputs for CPI as (type, cycles_event, instructions_event)."""
  return [
      ("intel_8pmc3", "APERF", "INST_RETIRED"),
      ("intel_8pmc3", "MPERF", "INST_RETIRED"),
      ("intel_4pmc3", "APERF", "INST_RETIRED"),
      ("intel_4pmc3", "MPERF", "INST_RETIRED"),
      ("amd64_pmc", "APERF", "INST_RETIRED"),
      ("amd64_pmc", "MPERF", "INST_RETIRED"),
      ("cpu_counter_metrics", "APERF", "INST_RETIRED"),
      ("cpu_counter_metrics", "MPERF", "INST_RETIRED"),
  ]


def _build_dynamic_candidates(jt):
  """Discover candidate (type, cycles_event, instructions_event) from jt.schema."""
  schema = getattr(jt, "schema", {}) or {}
  discovered = []
  for typ, events in schema.items():
    evset = set(events or [])
    if "INST_RETIRED" not in evset:
      continue
    if "APERF" in evset:
      discovered.append((typ, "APERF", "INST_RETIRED"))
    if "MPERF" in evset:
      discovered.append((typ, "MPERF", "INST_RETIRED"))
  return discovered


def _aggregate_counter_df(jt, typ, event):
  """Get aggregate counter data from arc deltas."""
  agg = jt.get_aggregate_df(typ, "arc", [event], 1.0)
  return agg, "arc"


def _host_cpi_series(schema, stats):
  """Per-interval CPI (length nt-1); caller extends to length nt. Returns None if unsupported."""
  events = frozenset(schema.events)
  if "CLOCKS_UNHALTED_CORE" in events:
    instr_names = ("INSTRUCTIONS_RETIRED", "INST_RETIRED")
    for inm in instr_names:
      if inm in events:
        ci = schema["CLOCKS_UNHALTED_CORE"].index
        ii = schema[inm].index
        return numpy.diff(stats[:, ci]) / numpy.diff(stats[:, ii])
  instr_idx = None
  for inm in ("INSTRUCTIONS_RETIRED", "INST_RETIRED"):
    if inm in events:
      instr_idx = schema[inm].index
      break
  if instr_idx is None:
    return None
  for cyc in ("APERF", "MPERF"):
    if cyc in events:
      ci = schema[cyc].index
      return numpy.diff(stats[:, ci]) / numpy.diff(stats[:, instr_idx])
  return None


class HeatMap():
  """Builds a Bokeh heatmap of CPI (cycles/instruction) by host and time from a utils-compatible job.

    """

  def plot(self, job):
    """Compute per-host CPI from PMC (legacy Intel fixed, or APERF/MPERF + retired), return a Bokeh figure.

        """
    u = utils.utils(job)
    schema, _stats = u.get_type("pmc")

    host_cpi = []
    for hostname in u.hostnames:
      stats = _stats.get(hostname)
      if stats is None:
        return None
      cpi = _host_cpi_series(schema, stats)
      if cpi is None:
        return None
      host_cpi.append(numpy.append(cpi, cpi[-1]))
    if not host_cpi:
      return None
    host_cpi = numpy.array(host_cpi).flatten()
    host_cpi = numpy.nan_to_num(host_cpi)
    times = (job.times - job.times[0]).astype(str)
    data = ColumnDataSource(
        dict(hostnames=[h for host in u.hostnames for h in [host] * len(times)],
             times=list(times) * len(u.hostnames),
             cpi=host_cpi))

    cpi_hover = new_plain_number_hover_formatter()
    hover = HoverTool(
        tooltips=[("host", "@hostnames"), ("time", "@times"), ("cpi", "@cpi{custom}")],
        formatters={"@cpi": cpi_hover},
    )

    mapper = LinearColorMapper(
        palette=_HEATMAP_PALETTE, low=_HEATMAP_CPI_LOW, high=_HEATMAP_CPI_HIGH)
    colors = {"field": "cpi", "transform": mapper}

    hm = figure(
        **figure_embed_kw(
            320,
            title=_heatmap_figure_title(host_cpi.mean()),
            x_range=times,
            x_axis_label="Time",
            y_axis_label="Host",
            logo=None,
            y_range=u.hostnames,
            tools=[hover],
        ),
    )

    hm.rect("times",
            "hostnames",
            source=data,
            width=1,
            height=1,
            line_color=None,
            fill_color=colors)

    hm.axis.axis_line_color = None
    hm.axis.major_tick_line_color = None
    hm.axis.major_label_text_font_size = "5pt"
    hm.axis.major_label_standoff = 0
    hm.xaxis.major_label_orientation = 1.0

    return hm


def plot_and_reason_from_jid_table(jt):
  """Build CPI heatmap from jid_table (ORM).

  Returns (figure_or_none, unavailable_reason_or_none).
  """
  if not jt.host_list:
    return (None, "No hosts found in host_data for this job/time range")

  attempts = []
  candidates = _candidate_series()
  seen = set(candidates)
  for cand in _build_dynamic_candidates(jt):
    if cand not in seen:
      candidates.append(cand)
      seen.add(cand)

  # Try intel then amd PMC for cycles and instructions.
  for typ, cyc_event, instr_event in candidates:
    agg_cyc, cyc_src = _aggregate_counter_df(jt, typ, cyc_event)
    agg_instr, instr_src = _aggregate_counter_df(jt, typ, instr_event)
    cyc_rows = 0 if agg_cyc is None else len(agg_cyc.index)
    instr_rows = 0 if agg_instr is None else len(agg_instr.index)
    attempts.append(
        f"{typ}:{cyc_event}/{instr_event} rows(cyc={cyc_rows}, instr={instr_rows}) src(cyc={cyc_src}, instr={instr_src})"
    )
    if agg_cyc.empty or agg_instr.empty or "sum_val" not in agg_cyc.columns or "sum_val" not in agg_instr.columns:
      continue
    cyc = agg_cyc.rename(columns={"sum_val": "cycles"})[["host", "time", "cycles"]]
    instr = agg_instr.rename(columns={"sum_val": "instr"})[["host", "time", "instr"]]
    merged = cyc.merge(instr, on=["host", "time"], how="inner")
    if merged.empty:
      continue
    merged = merged.sort_values("time")
    merged["cpi"] = merged["cycles"] / merged["instr"].replace(0, numpy.nan)
    merged["cpi"] = merged["cpi"].fillna(0)
    merged["time_str"] = merged["time"].astype(str)
    data_times = merged["time_str"].unique().tolist()
    times = _build_heatmap_time_axis_labels(data_times, jt)
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
    cpi_hover = new_plain_number_hover_formatter()
    hover = HoverTool(
        tooltips=[("host", "@hostnames"), ("time", "@times"), ("cpi", "@cpi{custom}")],
        formatters={"@cpi": cpi_hover},
    )
    mapper = LinearColorMapper(
        palette=_HEATMAP_PALETTE, low=_HEATMAP_CPI_LOW, high=_HEATMAP_CPI_HIGH)
    mean_cpi = numpy.nanmean(cpi_flat) if cpi_flat else 0
    hm = figure(
        **figure_embed_kw(
            320,
            title=_heatmap_figure_title(mean_cpi),
            x_range=times,
            y_range=hostnames,
            x_axis_label="Time",
            y_axis_label="Host",
            tools=[hover],
        ),
    )
    hm.rect(
        "times",
        "hostnames",
        source=source,
        width=1,
        height=1,
        line_color=None,
        fill_color={"field": "cpi", "transform": mapper},
    )
    hm.axis.axis_line_color = None
    hm.axis.major_tick_line_color = None
    hm.axis.major_label_text_font_size = "5pt"
    hm.axis.major_label_standoff = 0
    hm.xaxis.major_label_orientation = 1.0
    return (hm, None)

  attempts_msg = "; ".join(attempts)
  return (
      None,
      "Missing CPI counters in host_data (need APERF or MPERF plus INST_RETIRED in "
      "intel_*pmc3, amd64_pmc, or cpu_counter_metrics arc data). Attempted: "
      + attempts_msg,
  )


def plot_from_jid_table(jt):
  """Return only the figure (or None)."""
  fig, _reason = plot_and_reason_from_jid_table(jt)
  return fig
