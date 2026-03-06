"""Metric computation for jobs: simple metrics (job_arc/time_bucket) and complex metrics (avg_freq, avg_ethbw, mem_hwm, etc.) via utils-compatible job view. Results written to metrics_data.

AI generated.
"""
import hpcperfstats.conf_parser as cfg
openblas_threads = int(cfg.get_total_cores())/4
if openblas_threads < 1:
    openblas_threads = 1

import multiprocessing
import os
os.environ['OPENBLAS_NUM_THREADS'] = str(openblas_threads)

import sys
import time

import numpy as np
from numpy import amax, diff, isnan, maximum, mean, zeros
from pandas import to_datetime

from django.db import transaction

from hpcperfstats.analysis.gen import jid_table
from hpcperfstats.analysis.gen.utils import utils
from hpcperfstats.site.machine.models import metrics_data

try:
    from numpy import trapezoid as trapz
except ImportError:
    from numpy import trapz




class _EventIndex:
    """Holds the integer index of an event in a schema. Used by _Schema.__getitem__.

    AI generated.
    """
  def __init__(self, index):
    self.index = index


class _Schema:
    """Schema for a type: list of event names and a name->index mapping.

    AI generated.
    """
  def __init__(self, events):
    self.events = list(events)
    self._index = {name: idx for idx, name in enumerate(self.events)}
    self.desc = " ".join(self.events) + "\n"

  def __getitem__(self, name):
    return _EventIndex(self._index[name])


class _Host:
    """Minimal host container with a stats dict (typename -> dev -> array).

    AI generated.
    """
  def __init__(self):
    self.stats = {}


class _JobForMetrics:
    """Minimal job-like object compatible with hpcperfstats.analysis.gen.utils.utils. Built from jid_table full host_data DataFrame.

    AI generated.
    """

  def __init__(self, jt):
    self.jid = jt.jid
    self.hosts = {}
    self.schemas = {}
    self.acct = {"cores": 1, "nodes": 1}

    df = jt.get_full_host_data_df(columns=["host", "time", "type", "event", "value"])
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
    """Wrapper for pool: call compute_metrics on the job. Used by Metrics.run.

    AI generated.
    """
    return args[0].compute_metrics(args[1])

class Metrics():
    """Computes simple and complex metrics for a list of jobs in parallel and writes results to metrics_data.

    AI generated.
    """

  def __init__(self):
    """Initialize simple_metrics_list and complex_metrics_list.

    AI generated.
    """
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
    """Run metric computation for each job in job_list in a process pool; persist results via metrics_data.update_or_create.

    AI generated.
    """
    if not job_list:
      print("Please specify a job list.")
      return

    threads = int(int(cfg.get_total_cores())/2)
    if threads < 1:
      threads = 1

    with multiprocessing.Pool(processes=threads) as pool:
      for job_results in pool.imap_unordered(_unwrap, ((self, job) for job in job_list)):
        if not job_results:
          continue
        # Perform all ORM writes in the main process to avoid
        # database-connection and race issues across forked workers.
        with transaction.atomic():
          for item in job_results:
            metrics_data.objects.update_or_create(
              jid=item["jid"],
              type=item["type"],
              metric=item["metric"],
              defaults={
                "units": item["units"],
                "value": item["value"],
              },
            )


  def job_arc(self, jt, name=None, typename=None, events=None, conv=0, units=None):
    """Aggregate arc by host and 5m time bucket via Django DB connection (TimescaleDB time_bucket). Returns mean of per-host mean sum_val, or None.

    AI generated.
    """
    from django.db import connection
    import pandas as pd

    if not getattr(jt, "_base_filter", None):
      return None
    # Use raw SQL for time_bucket (TimescaleDB); params to avoid injection
    with connection.cursor() as cur:
      cur.execute(
        """
        SELECT host, time_bucket('5m', time) AS time, sum(arc) * %s AS sum
        FROM host_data
        WHERE time >= %s AND time <= %s AND host = ANY(%s) AND type = %s AND event = ANY(%s)
        GROUP BY host, time
        ORDER BY host, time
        """,
        [
          conv,
          jt._base_filter["time__gte"],
          jt._base_filter["time__lte"],
          jt._base_filter["host__in"],
          typename,
          list(events),
        ],
      )
      rows = cur.fetchall()
    if not rows:
      return None
    df = pd.DataFrame(rows, columns=["host", "time", "sum"])
    # Drop first time sample from each host
    df = df.groupby("host", group_keys=False).apply(lambda g: g.iloc[1:]).reset_index(drop=True)
    if df.empty:
      return None
    df_n = df.groupby("host")["sum"].mean()
    return float(df_n.mean())

  # Compute metric
  def compute_metrics(self, job):
    """Compute all simple and complex metrics for one job using jid_table and utils; return list of result dicts for metrics_data.

    AI generated.
    """
    metric_compute_start = time.time()

    results = []

    # Job-scoped host_data via ORM (no temp table)
    with jid_table.jid_table(job.jid) as jt:

      job_view = _JobForMetrics(jt)

      if job_view.times.size == 0:
        return []

      for metric_name, metric_obj in self.simple_metrics_list.items():
        value = self.job_arc(jt, **metric_obj)

        if value is None:
          continue

        results.append(
          {
            "jid": job,
            "type": metric_obj["typename"],
            "metric": metric_name,
            "units": metric_obj["units"],
            "value": value,
          }
        )

      u = utils(job_view) 

      for metric_name in self.complex_metrics_list:
        value, typename, units = getattr(sys.modules[__name__], metric_name)().compute_metric(u)

        if value is None:
          continue
        results.append(
          {
            "jid": job,
            "type": typename,
            "metric": metric_name,
            "units": units,
            "value": value,
          }
        )

    print("compute metrics time: {0:.1f}".format(time.time() - metric_compute_start))
    return results


###########
# Complex Metrics #
###########

class avg_freq():
    """Average CPU frequency (GHz) from PMC CLOCKS_UNHALTED_CORE/CLOCKS_UNHALTED_REF.

    AI generated.
    """
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'GHz'
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
    """Average Ethernet bandwidth (MB/s) from net rx_bytes/tx_bytes.

    AI generated.
    """
    def compute_metric(self, u):
        typename = "net"
        schema, _stats = u.get_type(typename)
        if schema is None: return None, typename,'MB/s'
        bw = 0
        for hostname, stats in _stats.items():
            bw += stats[-1, schema["rx_bytes"].index] - stats[0, schema["rx_bytes"].index] + \
                  stats[-1, schema["tx_bytes"].index] - stats[0, schema["tx_bytes"].index]
        value = bw/(u.dt*u.nhosts*1024*1024)
        if value == 0: return None, typename,'MB/s'
        return value, typename,'MB/s'

class avg_gpuutil():
    """Average GPU utilization (%) from nvidia_gpu utilization.

    AI generated.
    """
    def compute_metric(self, u):
        typename = "nvidia_gpu"
        schema, _stats = u.get_type(typename)
        if schema is None: return None, typename,'%'
        util = 0
        for hostname, stats in _stats.items():
            util += mean(stats[1:-1, schema["utilization"].index])
        value = util/u.nhosts
        if value == 0: return None, typename,'MB/s'
        return value, typename,'%'


class avg_packetsize():
    """Average packet size (MB) from ib_ext or opa port xmit/rcv data and packets.

    AI generated.
    """
  def compute_metric(self, u):
    try:
      typename = "ib_ext"
      schema, _stats = u.get_type(typename)
      if schema is None: return None, typename,'MB'
      tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
      tb, rb = schema["port_xmit_data"].index, schema["port_rcv_data"].index
      conv2mb = 1024*1024
    except:
      typename = "opa"
      schema, _stats = u.get_type(typename)
      if schema is None: return None, typename,'MB'
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
    """Maximum fabric bandwidth (MB/s) from ib_ext or opa port data.

    AI generated.
    """
    def compute_metric(self, u):
        max_bw=0
        try:
            typename = "ib_ext"
            schema, _stats = u.get_type(typename)
            if schema is None: return None, typename,'MB'
            tx, rx = schema["port_xmit_data"].index, schema["port_rcv_data"].index
            conv2mb = 1024*1024
        except:
            typename = "opa"
            schema, _stats = u.get_type(typename)
            if schema is None: return None, typename,'MB'
            tx, rx = schema["PortXmitData"].index, schema["PortRcvData"].index
            conv2mb = 125000
        for hostname, stats in _stats.items():
            max_bw = max(max_bw, amax(diff(stats[:, tx] + stats[:, rx])/diff(u.t)))
        if max_bw == 0: return None, typename,'MB/s'
        value = max_bw/conv2mb
        return value, typename,'MB/s'

class max_lnetbw():
    """Maximum LNET bandwidth (MB/s) from lnet tx_bytes/rx_bytes.

    AI generated.
    """
    def compute_metric(self, u):
        typename = "lnet"
        schema, _stats = u.get_type(typename)
        if schema is None: return None, typename,'MB/s'
        max_bw=0.0
        tx, rx = schema["tx_bytes"].index, schema["rx_bytes"].index
        for hostname, stats in _stats.items():
            max_bw = max(max_bw, amax(diff(stats[:, tx] + stats[:, rx])/diff(u.t)))
        if max_bw == 0: return None, typename,'MB/s'
        value = max_bw/(1024*1024)
        return value, typename,'MB/s'

class max_mds():
    """Maximum Lustre MDS operations (iops) from llite open/close/mmap/fsync/... events.

    AI generated.
    """
  def compute_metric(self, u):
    max_mds = 0
    typename = "llite"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'iops'
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
    if max_mds == 0: return None, typename,'iops'
    value = max_mds
    return value, typename,'iops'

class max_packetrate():
    """Maximum packet rate (#/s) from ib_ext or opa port xmit/rcv packets.

    AI generated.
    """
    def compute_metric(self, u):
        max_pr=0
        try:
            typename = "ib_ext"
            schema, _stats = u.get_type(typename)
            if schema is None: return None, typename,'#/s'
            tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
        except:
            typename = "opa"
            schema, _stats = u.get_type(typename)
            if schema is None: return None, typename,'#/s'
            tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index

        for hostname, stats in _stats.items():
            max_pr = max(max_pr, amax(diff(stats[:, tx] + stats[:, rx])/diff(u.t)))
        if max_pr == 0:    return None, typename,'#/s'
        value = max_pr
        return value, typename,'#/s'

# This will compute the maximum memory usage recorded
# by monitor.  It only samples at x mn intervals and
# may miss high water marks in between.
class mem_hwm():
    """Memory high-water mark (GiB) from mem MemUsed - Slab - FilePages.

    AI generated.
    """
  def compute_metric(self, u):
    # mem usage in GB
    max_memusage = 0.0
    typename = "mem"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'GiB'
    for hostname, stats in _stats.items():
      max_memusage = max(max_memusage,
                         amax(stats[:, schema["MemUsed"].index] - \
                              stats[:, schema["Slab"].index] - \
                              stats[:, schema["FilePages"].index]))
    if max_memusage == 0:
      return None, typename,'GiB'
    value = max_memusage/(2.**30)
    return value, typename,'GiB'

class node_imbalance():
    """CPU node imbalance (%): max deviation of per-node CPU rate from max rate.

    AI generated.
    """
  def compute_metric(self, u):
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'%'
    max_usage = zeros(u.nt - 1)
    for hostname, stats in _stats.items():
      max_usage = maximum(max_usage, diff(stats[:, schema["user"].index])/diff(u.t))

    max_imbalance = []
    for hostname, stats in _stats.items():
      max_imbalance += [mean((max_usage - diff(stats[:, schema["user"].index])/diff(u.t))/max_usage)]
    if max_imbalance == []:
      return None, typename,'%'
    value = 100*amax([0. if isnan(x) else x for x in max_imbalance])
    return value, typename,'%'

class time_imbalance():
    """CPU time imbalance (%): minimum ratio of integral after/before a time slice.

    AI generated.
    """
  def compute_metric(self, u):
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'%'
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
    """Percentage of 64b vectorized FLOPs vs total (from PMC events).

    AI generated.
    """
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'#'
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
      return None, typename,'#'
    value = 100*vector_flops/denom
    return value, typename,'%'

class avg_vector_width_64b():
    """Average 64b vector width (FLOPs-weighted) from PMC events.

    AI generated.
    """
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'#'
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
    """Percentage of 32b vectorized FLOPs vs total (from PMC events).

    AI generated.
    """
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'#'
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
    """Average 32b vector width (FLOPs-weighted) from PMC events.

    AI generated.
    """
  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None: return None, typename,'#'
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


