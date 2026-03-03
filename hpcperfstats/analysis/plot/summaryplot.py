#!/usr/bin/env python3
import math
import os
import time

from pandas import read_sql, to_datetime

os.environ['OPENBLAS_NUM_THREADS'] = '4'
#from hpcperfstats.analysis.gen.utils import read_sql, clean_dataframe

from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, Range1d
from bokeh.models import CustomJSTickFormatter
from bokeh.models.glyphs import Step
from bokeh.palettes import d3
from bokeh.plotting import figure

import hpcperfstats.conf_parser as cfg

local_timezone = cfg.get_timezone()

def _make_local_time_tick_formatter():
    # Must return a fresh model per plot/document (Bokeh models cannot be shared
    # across documents, e.g. across separate web requests).
    return CustomJSTickFormatter(
        args={"tz": local_timezone},
        code="""
// Bokeh datetimes are milliseconds since epoch. Render tick labels in tz.
const dt = new Date(tick)

function pad2(n) { return (n < 10) ? ("0" + n) : ("" + n) }

try {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }).formatToParts(dt)

  const out = {}
  for (const p of parts) out[p.type] = p.value
  return `${out.hour}:${out.minute}`
} catch (e) {
  // Fallback: UTC without Intl timezone support or invalid tz name.
  return `${pad2(dt.getUTCHours())}:${pad2(dt.getUTCMinutes())}`
}
""",
    )



class SummaryPlot():

  def __init__(self, jt):
    self.jid = jt.jid
    self.conn = jt.conj
    self.host_list = jt.host_list

  def plot_metric(self, df, metric, label):
    s = time.time()

    df = df[["time", "host", metric]]
    #df = clean_dataframe(df)

    y_range_end = 1.1*df[metric].max()
    if math.isnan(y_range_end):
        y_range_end = 0

    plot = figure(width=400, height=150, x_axis_type = "datetime",
                  y_range = Range1d(-0.1, y_range_end), y_axis_label = label)
    plot.xaxis.formatter = _make_local_time_tick_formatter()

    for h in self.host_list:
      source = ColumnDataSource(df[df.host == h])
      plot.add_glyph(source, Step(x = "time", y = metric, mode = "before", line_color = self.hc[h]))
      #plot.line(source = source, x = "time", y = metric, line_color = self.hc[h])
    print("time to plot {0}: {1}".format(metric, time.time() -s))
    return plot

  def plot(self):

    self.hc = {}
    colors = d3["Category20"][20]
    for i, hostname in enumerate(self.host_list):
      self.hc[hostname] = colors[i%20]

    print("Host Count:", len(self.host_list))

    metrics = [
      ("amd64_pmc", "arc", ['FLOPS'], "amd_flops", 1e-9, "FLOPS32b+64b[GF]"),
      ("amd64_df", "arc", ['MBW_CHANNEL_0', 'MBW_CHANNEL_1', 'MBW_CHANNEL_2', 'MBW_CHANNEL_3'],
       "amd_mbw", 2/(1024*1024*1024), "DRAMBW[GB/s]"),
      ("amd64_pmc", "value", ['INST_RETIRED'], "amd_instr", 1, '[#/s]'),
      ("amd64_pmc", "arc", ['MPERF'], "amd_mcycles", 1, '[#/s]'),
      ("amd64_pmc", "arc", ['APERF'], "amd_acycles", 1, '[#/s]'),
      #("amd64_rapl", "arc", ['MSR_PKG_ENERGY_STAT'], "amd_watts", 0.00001526, '[watts]'),

      ("intel_8pmc3", "arc", ['FP_ARITH_INST_RETIRED_SCALAR_DOUBLE', 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE', 'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE', 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE'], "flops64b", 1e-9, "FLOPS64b[GF]"),
      ("intel_8pmc3", "arc", ['FP_ARITH_INST_RETIRED_SCALAR_SINGLE', 'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE', 'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE', 'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE'], "flops32b", 1e-9, "FLOPS32b[GF]"),
      ("intel_8pmc3", "arc", ['INST_RETIRED'], "instr", 1, '[#/s]'),
      ("intel_8pmc3", "arc", ['MPERF'], "mcycles", 1, '[#/s]'),
      ("intel_8pmc3", "arc", ['APERF'], "acycles", 1, '[#/s]'),
      ("intel_rapl", "arc", ['MSR_PKG_ENERGY_STATUS'], "watts", 0.00001526, '[watts]'),
      ("intel_skx_imc", "arc", ['CAS_READS', 'CAS_WRITES'], "mbw", 64/(1024*1024*1024), "DRAMBW[GB/s]"),

      ("ib_ext", "arc", ['port_rcv_data', 'port_xmit_data'], "ibbw", 1/(1024*1024), "FabricBW[MB/s]"),
      ("llite", "arc", ['open', 'close', 'mmap', 'fsync' , 'setattr', 'truncate', 'flock', 'getattr' , 'statfs', 'alloc_inode', 'setxattr', 'listxattr', 'removexattr', 'readdir', 'create', 'lookup', 'link', 'unlink', 'symlink', 'mkdir', 'rmdir', 'mknod', 'rename'], "liops", 1, "LustreIOPS[#/s]"),
      ("llite", "arc", ['read_bytes', 'write_bytes'], "lbw", 1/(1024*1024), "LustreBW[MB/s]"),
      ("cpu", "arc", ['user', 'system', 'nice'], "cpu", 0.01, "CPU Usage [#cores]"),
      ("mem", "value", ['MemUsed'], "mem", 1/(1024*1024), "MemUsed[GB]")
    ]

    df = read_sql("select host, time from job_{0} group by host, time order by host, time".format(self.jid), self.conn)

    for typ, val, events, name, conv, label in metrics:
      s = time.time()
      df[name] = conv*read_sql("select sum({0}) from job_{3} where type = '{1}' and event in ('{2}') \
      group by host, time order by host, time".format(val, typ, "','".join(events), self.jid), self.conn)

      if name == "amd_watts": print(df[name])
      if df[name].isnull().values.any():
        del df[name]
      print("time to compute {0}: {1}".format(name, time.time() -s))

    if 'acycles' in df.columns and 'mcycles' in df.columns:
      df["freq"]  = 2.7*df["acycles"]/df["mcycles"]
      df["cpi"]  = df["acycles"]/df["instr"]
      metrics += [("freq", "arc", [], "freq", 1, "[GHz]")]
      metrics += [("cpi", "arc", [], "cpi", 1, "CPI")]
      del df["mcycles"], df["acycles"], df["instr"]

    if 'amd_acycles' in df.columns and 'amd_mcycles' in df.columns:
      # instructions retired counter is unreliable in AMD 19h
      # Revision Guide for AMD Family 19h Models 00h-0Fh Processors
      # df["cpi"]  = df["amd_acycles"]/df["amd_instr"]
      # metrics += [("cpi", "arc", [], "cpi", 1, "CPI")]

      #df["freq"]  = 2.45*df["amd_acycles"]/df["amd_mcycles"]
      #metrics += [("freq", "arc", [], "freq", 1, "[GHz]")]

      del df["amd_mcycles"], df["amd_acycles"], df["amd_instr"]
    df = df.reset_index()

    df["time"] = to_datetime(df["time"], utc = True)
    df["time"] = df["time"].dt.tz_convert(local_timezone)
#    df = clean_dataframe(df)


    plots = []
    for typ, val, events, name, conv, label in metrics:
      if name not in df.columns: continue
      plots += [self.plot_metric(df, name, label)]

    return gridplot(plots, ncols = len(plots)//4 + 1)
