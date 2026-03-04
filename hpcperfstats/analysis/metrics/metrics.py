import os
os.environ['OPENBLAS_NUM_THREADS'] = '4'

import sys
import time

import numpy as np
from numpy import amax, diff, isnan, maximum, mean, zeros
from pandas import to_datetime

from hpcperfstats.analysis.gen import jid_table
from hpcperfstats.analysis.gen.utils import read_sql, utils
from hpcperfstats.site.machine.models import metrics_data

try:
    from numpy import trapezoid as trapz
except ImportError:
    from numpy import trapz




class _EventIndex:
  def __init__(self, index):
    self.index = index


class _Schema:
  def __init__(self, events):
    self.events = list(events)
    self._index = {name: idx for idx, name in enumerate(self.events)}
    self.desc = " ".join(self.events) + "\n"

  def __getitem__(self, name):
    return _EventIndex(self._index[name])


class _Host:
  def __init__(self):
    self.stats = {}


class _JobForMetrics:
  """Minimal job-like object compatible with hpcperfstats.analysis.gen.utils.utils."""

  def __init__(self, jt):
    self.jid = jt.jid
    self.hosts = {}
    self.schemas = {}
    self.acct = {"cores": 1, "nodes": 1}

    df = read_sql(
      "select host, time, type, event, value from job_{0}".format(self.jid),
      jt.conj,
    )
    if df.empty:
      self.times = np.array([])
      return

    # Global sorted time axis
    df = df.sort_values("time")
    df["time"] = to_datetime(df["time"]).dt.tz_localize(None)
    times = df["time"].drop_duplicates().sort_values()

    # Use float seconds (NumPy) for simplicity; utils only uses differences
    times_values = times.values.astype("datetime64[s]")
    self.times = times_values.astype("float64")

    # Build schemas based on jt.schema (type -> [events])
    for typename, events in jt.schema.items():
      self.schemas[typename] = _Schema(events)

    # Prepare host containers
    host_list = df["host"].drop_duplicates().values
    for host in host_list:
      self.hosts[host] = _Host()

    # Populate stats arrays per (host, type)
    for typename, schema in self.schemas.items():
      events = schema.events
      nevents = len(events)
      if nevents == 0:
        continue

      type_df = df[df["type"] == typename]
      if type_df.empty:
        continue

      event_index = {name: idx for idx, name in enumerate(events)}

      for host, host_df in type_df.groupby("host"):
        host_obj = self.hosts[host]
        # Single aggregated "dev" per host suitable for utils
        stats = np.zeros((len(self.times), nevents), dtype=float)

        # Align host samples to global time axis
        host_df = host_df.sort_values("time")
        time_to_row = {
          t: i for i, t in enumerate(times.values)
        }

        for _, row in host_df.iterrows():
          t = row["time"]
          if t not in time_to_row:
            continue
          ti = time_to_row[t]
          eve = row["event"]
          if eve not in event_index:
            continue
          ei = event_index[eve]
          stats[ti, ei] = row["value"]

        host_obj.stats.setdefault(typename, {})
        host_obj.stats[typename]["agg"] = stats


def _unwrap(args):
  return args[0].compute_metrics(args[1])
  #  return

class Metrics():

  def __init__(self):

    self.simple_metrics_list = {
      "avg_blockbw" : { "typename" : "block", "events" : ["rd_sectors", "wr_sectors"], "conv" : 1.0/(1024*1024), "units" : "GB/s"},
      "avg_cpuusage" : { "typename" : "cpu",   "events" : ["user", "system", "nice"], "conv" : 0.01, "units" : "#cores" },
      "avg_lustreiops" : { "typename" : "llite", "events" : [
        "open", "close", "mmap", "fsync" , "setattr", "truncate", "flock", "getattr" ,
        "statfs", "alloc_inode", "setxattr", "listxattr", "removexattr", "readdir",
        "create", "lookup", "link", "unlink", "symlink", "mkdir", "rmdir", "mknod", "rename"], "conv" : 1, "units" : "iops" },
      "avg_lustrebw" : { "typename" : "llite", "events" : ["read_bytes", "write_bytes"], "conv" : 1.0/(1024*1024), "units" : "MB/s"  },
      "avg_ibbw" : { "typename" : "ib_ext", "events" : ["port_xmit_data", "port_rcv_data"], "conv" : 1.0/(1024*1024), "units" : "MB/s"  },
      "avg_flops" : { "typename" : "amd64_pmc", "events" : ["FLOPS"], "conv" : 1e-9, "units" : "GF" },
      "avg_mbw" : { "typename" : "amd64_df", "events" : ["MBW_CHANNEL_0", "MBW_CHANNEL_1", "MBW_CHANNEL_2", "MBW_CHANNEL_3"], "conv" : 2/(1024*1024*1024), "units" : "GB/s" }
                  }

    self.complex_metrics_list = ['avg_freq', 'avg_ethbw', 'avg_gpuutil', 'avg_packetsize', 'max_fabricbw', 
                                 'max_lnetbw', 'max_mds', 'max_packetrate', 'mem_hwm', 'node_imbalance', 
                                 'time_imbalance', 'vecpercent_64b', 'avg_vector_width_64b', 'vecpercent_32b', 
                                 'avg_vector_width_32b'
                                 ]

  # Compute metrics in parallel (Shared memory only)
  def run(self, job_list):
    if not job_list:
      print("Please specify a job list.")
      return
    list(map(self.compute_metrics, job_list))


  def job_arc(self, jt, name = None, typename = None, events = None, conv = 0, units = None):
    df = read_sql("select host, time_bucket('5m', time) as time, sum(arc)*{0} as sum from job_{1} where type = '{2}' and event in ('{3}') group by host, time".format(conv, jt.jid, typename, "','".join(events)), jt.conj)
    if df.empty: return None
    # Drop first time sample from each host
    df = df.groupby('host').apply(lambda group: group.iloc[1:])
    #df = df.reset_index(drop = True)


    df_n = df.groupby('host')["sum"].mean()
    node_mean, node_max, node_min = df_n.mean(), df_n.max(), df_n.min()

    return node_mean

  # Compute metric
  def compute_metrics(self, job):
    metric_compute_start = time.time()

    # build temporary job view
    with jid_table.jid_table(job.jid) as jt:

      job_view = _JobForMetrics(jt)

      if job_view.times.size == 0:
        return

      
      for metric, metric_obj in self.simple_metrics_list.items():
        value = self.job_arc(jt, **metric)

        if value is None:
          continue

        obj, created = metrics_data.objects.update_or_create(jid = job, type = metric["typename"], metric = metric,
                                                             defaults = {'units' : metric_obj["units"],
                                                                         'value' : value})

      u = utils(job_view) 

      for metric in self.complex_metrics_list:
        value, typename, units = getattr(sys.modules[__name__], metric)().compute_metric(u)

        if value is None:
          continue

        obj, created = metrics_data.objects.update_or_create(jid = job, type = typename, metric = metric,
                                                             defaults = {'units' : units,
                                                                         'value' : value})

    print("compute metrics time: {0:.1f}".format(time.time() - metric_compute_start))


###########
# Complex Metrics #
###########

class avg_freq():
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    cycles = 0
    cycles_ref = 0
    for hostname, stats in _stats.items():
      cycles += stats[-1, schema["CLOCKS_UNHALTED_CORE"].index] - \
                stats[0, schema["CLOCKS_UNHALTED_CORE"].index]
      cycles_ref += stats[-1, schema["CLOCKS_UNHALTED_REF"].index] - \
                    stats[0, schema["CLOCKS_UNHALTED_REF"].index]
    if cycles_ref == 0:
      return None, typename, 'GHz'
    value = u.freq*cycles/cycles_ref
    return value, typename,'GHz'

class avg_ethbw():
    def compute_metric(self, u):
        typename = "net"
        schema, _stats = u.get_type(typename)
        bw = 0
        for hostname, stats in _stats.items():
            bw += stats[-1, schema["rx_bytes"].index] - stats[0, schema["rx_bytes"].index] + \
                  stats[-1, schema["tx_bytes"].index] - stats[0, schema["tx_bytes"].index]
        value = bw/(u.dt*u.nhosts*1024*1024)
        return value, typename,'MB/s'

class avg_gpuutil():
    def compute_metric(self, u):
        typename = "nvidia_gpu"
        schema, _stats = u.get_type(typename)
        util = 0
        for hostname, stats in _stats.items():
            util += mean(stats[1:-1, schema["utilization"].index])
        value = util/u.nhosts
        return value, typename,'%'


class avg_packetsize():
  def compute_metric(self, u):
    try:
      typename = "ib_ext"
      schema, _stats = u.get_type(typename)
      tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
      tb, rb = schema["port_xmit_data"].index, schema["port_rcv_data"].index
      conv2mb = 1024*1024
    except:
      typename = "opa"
      schema, _stats = u.get_type(typename)
      tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index
      tb, rb = schema["PortXmitData"].index, schema["PortRcvData"].index
      conv2mb = 125000

    npacks = 0
    nbytes  = 0
    for hostname, stats in _stats.items():
      npacks += stats[-1, tx] + stats[-1, rx] - \
                stats[0, tx] - stats[0, rx]
      nbytes += stats[-1, tb] + stats[-1, rb] - \
                stats[0, tb] - stats[0, rb]
    if npacks == 0:
      return None, typename,'MB'
    value = nbytes/(npacks*conv2mb)
    return value, typename,'MB'

class max_fabricbw():
    def compute_metric(self, u):
        max_bw=0
        try:
            typename = "ib_ext"
            schema, _stats = u.get_type(typename)
            tx, rx = schema["port_xmit_data"].index, schema["port_rcv_data"].index
            conv2mb = 1024*1024
        except:
            typename = "opa"
            schema, _stats = u.get_type(typename)
            tx, rx = schema["PortXmitData"].index, schema["PortRcvData"].index
            conv2mb = 125000
        for hostname, stats in _stats.items():
            max_bw = max(max_bw, amax(diff(stats[:, tx] + stats[:, rx])/diff(u.t)))
        value = max_bw/conv2mb
        return value, typename,'MB/s'

class max_lnetbw():
    def compute_metric(self, u):
        typename = "lnet"
        schema, _stats = u.get_type(typename)
        max_bw=0.0
        tx, rx = schema["tx_bytes"].index, schema["rx_bytes"].index
        for hostname, stats in _stats.items():
            max_bw = max(max_bw, amax(diff(stats[:, tx] + stats[:, rx])/diff(u.t)))
        value = max_bw/(1024*1024)
        return value, typename,'MB/s'

class max_mds():
  def compute_metric(self, u):
    max_mds = 0
    typename = "llite"
    schema, _stats = u.get_type(typename)
    for hostname, stats in _stats.items():
      max_mds = max(max_mds, amax(diff(stats[:, schema["open"].index] + \
                                       stats[:, schema["close"].index] + \
                                       stats[:, schema["mmap"].index] + \
                                       stats[:, schema["fsync"].index] + \
                                       stats[:, schema["setattr"].index] + \
                                       stats[:, schema["truncate"].index] + \
                                       stats[:, schema["flock"].index] + \
                                       stats[:, schema["getattr"].index] + \
                                       stats[:, schema["statfs"].index] + \
                                       stats[:, schema["alloc_inode"].index] + \
                                       stats[:, schema["setxattr"].index] + \
                                       stats[:, schema["listxattr"].index] + \
                                       stats[:, schema["removexattr"].index] + \
                                       stats[:, schema["readdir"].index] + \
                                       stats[:, schema["create"].index] + \
                                       stats[:, schema["lookup"].index] + \
                                       stats[:, schema["link"].index] + \
                                       stats[:, schema["unlink"].index] + \
                                       stats[:, schema["symlink"].index] + \
                                       stats[:, schema["mkdir"].index] + \
                                       stats[:, schema["rmdir"].index] + \
                                       stats[:, schema["mknod"].index] + \
                                       stats[:, schema["rename"].index])/diff(u.t)))
    value = max_mds
    return value, typename,'iops'

class max_packetrate():
    def compute_metric(self, u):
        max_pr=0
        try:
            typename = "ib_ext"
            schema, _stats = u.get_type(typename)
            tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
        except:
            typename = "opa"
            schema, _stats = u.get_type(typename)
            tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index

        for hostname, stats in _stats.items():
            max_pr = max(max_pr, amax(diff(stats[:, tx] + stats[:, rx])/diff(u.t)))
        value = max_pr
        return value, typename,'#/s'

# This will compute the maximum memory usage recorded
# by monitor.  It only samples at x mn intervals and
# may miss high water marks in between.
class mem_hwm():
  def compute_metric(self, u):
    # mem usage in GB
    max_memusage = 0.0
    typename = "mem"
    schema, _stats = u.get_type(typename)
    for hostname, stats in _stats.items():
      max_memusage = max(max_memusage,
                         amax(stats[:, schema["MemUsed"].index] - \
                              stats[:, schema["Slab"].index] - \
                              stats[:, schema["FilePages"].index]))
    value = max_memusage/(2.**30)
    return value, typename,'GiB'

class node_imbalance():
  def compute_metric(self, u):
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    max_usage = zeros(u.nt - 1)
    for hostname, stats in _stats.items():
      max_usage = maximum(max_usage, diff(stats[:, schema["user"].index])/diff(u.t))

    max_imbalance = []
    for hostname, stats in _stats.items():
      max_imbalance += [mean((max_usage - diff(stats[:, schema["user"].index])/diff(u.t))/max_usage)]
    value = 100*amax([0. if isnan(x) else x for x in max_imbalance])
    return value, typename,'%'

class time_imbalance():
  def compute_metric(self, u):
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    tmid=(u.t[:-1] + u.t[1:])/2.0
    dt = diff(u.t)
    vals = []
    for hostname, stats in _stats.items():
      #skip first and last two time slices
      for i in [x + 2 for x in range(len(u.t) - 4)]:
        r1=range(i)
        r2=[x + i for x in range(len(dt) - i)]
        rate = diff(stats[:, schema["user"].index])/diff(u.t)
        # integral before time slice
        a = trapz(rate[r1], tmid[r1])/(tmid[i] - tmid[0])
        if a == 0:
          continue
        # integral after time slice
        b = trapz(rate[r2], tmid[r2])/(tmid[-1] - tmid[i])
        # ratio of integral after time over before time
        vals += [b/a]
    if vals:
      value = 100*min(vals)
      return value, typename,'%'
    else:
      return None, typename,'%'

class vecpercent_64b():
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    vector_widths = {"SSE_D_ALL" : 1, "SIMD_D_256" : 2,
                    "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE" : 1,
                     "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE" : 2,
                     "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE" : 4,
                     "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE" : 8,
                     "SSE_DOUBLE_SCALAR" : 1,
                     "SSE_DOUBLE_PACKED" : 2,
                     "SIMD_DOUBLE_256" : 4}
    vector_flops = 0.0
    scalar_flops = 0.0
    for hostname, stats in _stats.items():
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          flops = (stats[-1, index] - stats[0, index])*vector_widths[eventname]
          if vector_widths[eventname] > 1: vector_flops += flops
          else: scalar_flops += flops
    denom = scalar_flops + vector_flops
    if denom == 0:
      return None, typename,'%'
    value = 100*vector_flops/denom
    return value, typename,'%'

class avg_vector_width_64b():
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    vector_widths = {"SSE_D_ALL" : 1, "SIMD_D_256" : 2,
                    "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE" : 1,
                     "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE" : 2,
                     "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE" : 4,
                     "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE" : 8,
                     "SSE_DOUBLE_SCALAR" : 1,
                     "SSE_DOUBLE_PACKED" : 2,
                     "SIMD_DOUBLE_256" : 4}
    flops = 0.0
    instr = 0.0
    for hostname, stats in _stats.items():
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          instr += (stats[-1, index] - stats[0, index])
          flops += (stats[-1, index] - stats[0, index])*vector_widths[eventname]
    if instr == 0:
      return None, typename,'#'
    value = flops/instr
    return value, typename,'#'

class vecpercent_32b():
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    vector_widths = {"FP_ARITH_INST_RETIRED_SCALAR_SINGLE" : 1,
                     "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE" : 4,
                     "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE" : 8,
                     "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE" : 16}
    vector_flops = 0.0
    scalar_flops = 0.0
    for hostname, stats in _stats.items():
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          flops = (stats[-1, index] - stats[0, index])*vector_widths[eventname]
          if vector_widths[eventname] > 1: vector_flops += flops
          else: scalar_flops += flops
    denom = scalar_flops + vector_flops
    if denom == 0:
      return None, typename,'%'
    value = 100*vector_flops/denom
    return value, typename,'%'

class avg_vector_width_32b():
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    vector_widths = {"FP_ARITH_INST_RETIRED_SCALAR_SINGLE" : 1,
                     "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE" : 4,
                     "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE" : 8,
                     "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE" : 16}
    flops = 0.0
    instr = 0.0
    for hostname, stats in _stats.items():
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          instr += (stats[-1, index] - stats[0, index])
          flops += (stats[-1, index] - stats[0, index])*vector_widths[eventname]
    if instr == 0:
      return None, typename,'#'
    value = flops/instr
    return value, typename,'#'


