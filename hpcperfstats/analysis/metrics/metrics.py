"""Metric computation for jobs: simple metrics (job_arc/time_bucket) and complex metrics (avg_freq, avg_ethbw, mem_hwm, etc.) via utils-compatible job view. Results written to metrics_data.

DB access is process-safe: _unwrap runs in multiprocessing workers and calls close_old_connections() at entry so each worker uses a fresh connection for reads (e.g. job_arc); writes are done in the main process only.

"""
import hpcperfstats.conf_parser as cfg
from hpcperfstats.print_utils import log_print

import multiprocessing
import sys
import time

import numpy as np
from numpy import amax, diff, isnan, maximum, mean, zeros
from pandas import to_datetime

from django.db import transaction, close_old_connections
from django.db.utils import OperationalError, DatabaseError

from hpcperfstats.analysis.gen import jid_table
from hpcperfstats.analysis.gen.utils import utils
from hpcperfstats.site.machine.models import job_data, metrics_data

try:
  from numpy import trapezoid as trapz
except ImportError:
  from numpy import trapz

# Default (type, units) for complex metrics when building the catalog / no-time-series rows.
# Types match the primary telemetry source for each metric (see compute_metric classes).
_COMPLEX_PLACEHOLDER_TYPE_UNITS = {
    "avg_freq": ("pmc", "GHz"),
    "avg_ethbw": ("net", "MB/s"),
    "avg_gpuutil": ("nvidia_gpu", "%"),
    "avg_packetsize": ("ib_ext", "MB"),
    "max_fabricbw": ("ib_ext", "MB/s"),
    "max_lnetbw": ("lnet", "MB/s"),
    "max_mds": ("llite", "iops"),
    "max_packetrate": ("ib_ext", "#/s"),
    "mem_hwm": ("mem", "GiB"),
    "node_imbalance": ("cpu", "%"),
    "time_imbalance": ("cpu", "%"),
    "vecpercent_64b": ("pmc", "%"),
    "avg_vector_width_64b": ("pmc", "#"),
    "vecpercent_32b": ("pmc", "%"),
    "avg_vector_width_32b": ("pmc", "#"),
}

_COMPLEX_NO_DATA_REASONS = {
    "avg_freq": "No usable PMC telemetry for average CPU frequency",
    "avg_ethbw": "No usable network telemetry for average Ethernet bandwidth",
    "avg_gpuutil": "No usable GPU utilization telemetry",
    "avg_packetsize": "No usable InfiniBand/OPA telemetry for packet size",
    "max_fabricbw": "No usable fabric telemetry for peak bandwidth",
    "max_lnetbw": "No usable LNET telemetry for peak bandwidth",
    "max_mds": "No usable Lustre llite telemetry for MDS operation rate",
    "max_packetrate": "No usable fabric telemetry for peak packet rate",
    "mem_hwm": "No usable memory telemetry for high-water mark",
    "node_imbalance": "No usable CPU telemetry for node imbalance",
    "time_imbalance": "No usable CPU telemetry for time imbalance",
    "vecpercent_64b": "No usable PMC telemetry for 64b vector FLOP mix",
    "avg_vector_width_64b": "No usable PMC telemetry for 64b vector width",
    "vecpercent_32b": "No usable PMC telemetry for 32b vector FLOP mix",
    "avg_vector_width_32b": "No usable PMC telemetry for 32b vector width",
}

NO_TIME_SERIES_MSG = "No time-series telemetry for this job"
NO_SIMPLE_SAMPLES_MSG = (
    "No host_data samples for this metric in the job window"
)
METRIC_NOT_COMPUTED_YET = "Metric not computed"

# Intel FP_ARITH events summed for GFLOP/s when amd64_pmc FLOPS is unavailable.
_INTEL_FP_ARITH_EVENTS = [
    "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE",
    "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE",
    "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE",
    "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE",
    "FP_ARITH_INST_RETIRED_SCALAR_SINGLE",
    "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE",
    "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE",
    "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE",
]

# SNB/IVB-style SSE/AVX double proxies (same weights as roofline / vecpercent_64b).
_INTEL_LEGACY_SSE_FLOP_EVENTS = [
    ("SSE_DOUBLE_SCALAR", 1),
    ("SSE_DOUBLE_PACKED", 2),
    ("SIMD_DOUBLE_256", 4),
]


def _per_interval_rate(values, t):
  """Compute diff(values) / diff(t) without divide-by-zero.

  Sample pairs with non-positive delta-t (duplicate timestamps) yield NaN so
  callers can use nan-aware reductions or substitute zeros for integration.
  """
  dy = np.asarray(diff(values), dtype=np.float64)
  dt = np.asarray(diff(np.asarray(t, dtype=np.float64)), dtype=np.float64)
  out = np.full(dy.shape, np.nan, dtype=np.float64)
  np.divide(dy, dt, out=out, where=dt > 0)
  return out


def _peak_interval_rate_from_cluster_mean(u, typename, column_indices, divisor):
  """Peak dy/dt from sum of host-averaged columns at each global timestamp.

  Uses ``job.cluster_mean_by_type[typename]`` (see ``_JobForMetrics``). Falls
  back is left to callers when this returns None.
  """
  cmap = getattr(u.job, "cluster_mean_by_type", None) or {}
  cm = cmap.get(typename)
  if cm is None or cm.size == 0 or cm.shape[0] < 2:
    return None
  s = np.zeros(cm.shape[0], dtype=np.float64)
  for j in column_indices:
    if j < 0 or j >= cm.shape[1]:
      return None
    s = s + cm[:, j]
  ratio = _per_interval_rate(s, u.t)
  fin = ratio[np.isfinite(ratio)]
  if fin.size == 0:
    return None
  peak = float(fin.max())
  if divisor:
    peak = peak / float(divisor)
  return peak if peak > 0 else None


class _EventIndex:
  """Holds the integer index of an event in a schema. Used by _Schema.__getitem__.

    """

  def __init__(self, index):
    """Store the integer index for an event."""
    self.index = index


class _Schema:
  """Schema for a type: list of event names and a name->index mapping.

    """

  def __init__(self, events):
    """Build event list and name->index mapping from event names."""
    # Normalise event names to strings so that schema construction is robust
    # when upstream code passes non‑string labels (e.g. pandas.Timestamp).
    self.events = [str(e) for e in events]
    self._index = {name: idx for idx, name in enumerate(self.events)}
    self.desc = " ".join(self.events) + "\n"

  def __getitem__(self, name):
    """Return _EventIndex for the given event name."""
    return _EventIndex(self._index[name])


class _Host:
  """Minimal host container with a stats dict (typename -> dev -> array).

    """

  def __init__(self):
    """Initialize empty stats dict."""
    self.stats = {}


class _JobForMetrics:
  """Minimal job-like object compatible with hpcperfstats.analysis.gen.utils.utils. Built from jid_table full host_data DataFrame.

    """

  def __init__(self, jt):
    """Build job-like view from jid_table full host_data DataFrame."""
    self.jid = jt.jid
    self.hosts = {}
    self.schemas = {}
    # Per-typename (n_times, n_events): mean of `value` across hosts at each
    # global timestamp (for peak interval-rate metrics; avoids bogus diffs when
    # nodes share a time axis but sparse samples per host).
    self.cluster_mean_by_type = {}
    self.acct = {"cores": 1, "nodes": 1}

    df = jt.get_full_host_data_df(
        columns=["host", "time", "type", "event", "value"])
    # If there is no time information, we cannot build a valid time axis; treat
    # as no data for this job (avoids KeyError when sorting by missing column).
    if df.empty or "time" not in df.columns:
      self.times = np.array([])
      self.per_host_distinct_time_sum = 0
      self.cluster_mean_by_type = {}
      return

    # Global sorted time axis
    df = df.sort_values("time")
    df["time"] = to_datetime(df["time"]).dt.tz_localize(None)
    # Sample count for invalidation: per-host COUNT(DISTINCT time), summed
    # (same semantics as live host_data subquery in update_metrics).
    self.per_host_distinct_time_sum = int(
        df.groupby("host")["time"].nunique().sum()
    )
    times = df["time"].drop_duplicates().sort_values()

    # Use float seconds (NumPy) for simplicity; utils only uses differences
    self.times = times.values.astype("datetime64[s]").astype(np.float64)

    # Reduce memory: categorical for repeated string columns
    # large DataFrames with many repeated host/type/event values use less memory.
    for col in ("host", "type", "event"):
      if col in df.columns and df[col].dtype == object:
        df[col] = df[col].astype("category")

    # Build schemas based on jt.schema (type -> [events])
    for typename, events in jt.schema.items():
      self.schemas[typename] = _Schema(events)

    # Prepare host containers
    host_list = df["host"].drop_duplicates().values
    for host in host_list:
      self.hosts[host] = _Host()

    # Populate stats arrays per (host, type) via vectorized pivot/reindex
    times_index = times.values
    for typename, schema in self.schemas.items():
      events = schema.events
      nevents = len(events)
      if nevents == 0:
        continue

      type_df = df[df["type"] == typename]
      if type_df.empty:
        continue

      # Mean across hosts at each (time, event) for interval-rate peak metrics.
      pavg = type_df[["time", "event", "value"]].copy()
      pavg["time"] = to_datetime(pavg["time"]).dt.tz_localize(None)
      try:
        cluster_pivot = (
            pavg.groupby(["time", "event"])["value"].mean().unstack(fill_value=np.nan)
        )
        cluster_pivot = cluster_pivot.reindex(
            index=times, fill_value=np.nan
        ).reindex(columns=events, fill_value=np.nan)
        self.cluster_mean_by_type[typename] = np.ascontiguousarray(
            cluster_pivot.values, dtype=np.float64
        )
      except (ValueError, KeyError):
        self.cluster_mean_by_type[typename] = np.full(
            (len(times_index), len(events)), np.nan, dtype=np.float64
        )

      for host, host_df in type_df.groupby("host"):
        host_obj = self.hosts[host]
        pivot = host_df.pivot_table(
            index="time", columns="event", values="value", aggfunc="mean"
        )
        pivot = pivot.reindex(
            index=times_index, fill_value=0
        ).reindex(columns=events, fill_value=0)
        stats = np.ascontiguousarray(pivot.values, dtype=np.float64)
        host_obj.stats.setdefault(typename, {})
        host_obj.stats[typename]["agg"] = stats
      del type_df


def _unwrap(args):
  """Wrapper for pool: call compute_metrics on the job. Used by Metrics.run.

    """
  # Ensure this worker process uses a fresh DB connection (thread-safe for multiprocessing).
  close_old_connections()
  metrics_obj, job = args

  # Lost DB connections in worker processes can manifest as "lost
  # synchronization with server" DatabaseErrors. Retry once with a clean
  # connection; on repeated failure, log and skip this job rather than
  # crashing the entire pool.
  for attempt in range(2):
    try:
      return metrics_obj.compute_metrics(job)
    except (OperationalError, DatabaseError) as exc:
      close_old_connections()
      if attempt == 0:
        continue
      log_print(
          "Skipping metrics for jid %s after DB error in worker: %s" %
          (getattr(job, "jid", "?"), exc)
      )
      return None


def _persist_metrics_batch(job_results, distinct_time_count):
  """Fetch existing metrics_data for jids in job_results, then bulk_create/bulk_update.

  Also sets job_data.metrics_distinct_time_count for the job(s) in this batch.
  Called in main process only.
  """
  with transaction.atomic():
    jids = list({item["jid"].jid for item in job_results})
    existing = list(
        metrics_data.objects.filter(jid_id__in=jids).only(
            "id", "jid_id", "type", "metric", "units", "value", "no_data_reason"
        )
    )
    existing_by_key = {(r.jid_id, r.type, r.metric): r for r in existing}
    to_update_list = []
    to_create = []
    for item in job_results:
      key = (item["jid"].jid, item["type"], item["metric"])
      if key in existing_by_key:
        obj = existing_by_key[key]
        obj.units = item["units"]
        obj.value = item["value"]
        obj.no_data_reason = item.get("no_data_reason")
        to_update_list.append(obj)
      else:
        to_create.append(
            metrics_data(
                jid_id=item["jid"].jid,
                type=item["type"],
                metric=item["metric"],
                units=item["units"],
                value=item["value"],
                no_data_reason=item.get("no_data_reason"),
            )
        )
    if to_create:
      metrics_data.objects.bulk_create(to_create)
    if to_update_list:
      metrics_data.objects.bulk_update(
          list({id(o): o for o in to_update_list}.values()),
          ["units", "value", "no_data_reason"],
      )
    if distinct_time_count is not None and jids:
      jobs_up = list(job_data.objects.filter(pk__in=jids))
      for jo in jobs_up:
        jo.metrics_distinct_time_count = distinct_time_count
      job_data.objects.bulk_update(jobs_up, ["metrics_distinct_time_count"])


class Metrics():
  """Computes simple and complex metrics for a list of jobs in parallel and writes results to metrics_data.

    """

  def __init__(self):
    """Initialize simple_metrics_list and complex_metrics_list.

        """
    self.simple_metrics_list = {
        "avg_blockbw": {
            "typename": "block",
            "events": ["rd_sectors", "wr_sectors"],
            "conv": 1.0 / (1024 * 1024),
            "units": "GB/s"
        },
        "avg_cpuusage": {
            "typename": "cpu",
            "events": ["user", "system", "nice"],
            "conv": 0.01,
            "units": "#cores"
        },
        "avg_lustreiops": {
            "typename": "llite",
            "events": [
                "open", "close", "mmap", "fsync", "setattr", "truncate",
                "flock", "getattr", "statfs", "alloc_inode", "setxattr",
                "listxattr", "removexattr", "readdir", "create", "lookup",
                "link", "unlink", "symlink", "mkdir", "rmdir", "mknod", "rename"
            ],
            "conv": 1,
            "units": "iops"
        },
        "avg_lustrebw": {
            "typename": "llite",
            "events": ["read_bytes", "write_bytes"],
            "conv": 1.0 / (1024 * 1024),
            "units": "MB/s"
        },
        "avg_ibbw": {
            "typename": "ib_ext",
            "events": ["port_xmit_data", "port_rcv_data"],
            "conv": 1.0 / (1024 * 1024),
            "units": "MB/s"
        },
        "avg_flops": {
            "typename": "amd64_pmc",
            "events": ["FLOPS"],
            "conv": 1e-9,
            "units": "GF"
        },
        "avg_mbw": {
            "typename": "amd64_df",
            "events": [
                "MBW_CHANNEL_0", "MBW_CHANNEL_1", "MBW_CHANNEL_2",
                "MBW_CHANNEL_3"
            ],
            "conv": 2 / (1024 * 1024 * 1024),
            "units": "GB/s"
        }
    }

    self.complex_metrics_list = [
        'avg_freq', 'avg_ethbw', 'avg_gpuutil', 'avg_packetsize',
        'max_fabricbw', 'max_lnetbw', 'max_mds', 'max_packetrate', 'mem_hwm',
        'node_imbalance', 'time_imbalance', 'vecpercent_64b',
        'avg_vector_width_64b', 'vecpercent_32b', 'avg_vector_width_32b'
    ]

  # Compute metrics in parallel (Shared memory only)
  def run(self, job_list):
    """Run metric computation for each job in job_list in a process pool; persist results via metrics_data.update_or_create.

        """
    if not job_list:
      log_print("Please specify a job list.")
      return

    threads = int(int(cfg.get_total_cores()) / 2)
    if threads < 1:
      threads = 1

    with multiprocessing.Pool(processes=threads) as pool:
      for payload in pool.imap_unordered(_unwrap,
                                         ((self, job) for job in job_list)):
        if not payload:
          continue
        job_rows = payload["rows"]
        distinct_n = payload.get("distinct_time_count")
        if not job_rows:
          continue
        # Ensure main process uses a fresh DB connection (may have gone stale
        # while waiting on pool). Retry once on connection errors.
        for attempt in range(2):
          try:
            close_old_connections()
            _persist_metrics_batch(job_rows, distinct_n)
            break
          except OperationalError as e:
            if "connection" in str(e).lower() or "closed" in str(e).lower():
              close_old_connections()
              if attempt == 0:
                continue
            raise

  def job_arc(self,
              jt,
              name=None,
              typename=None,
              events=None,
              conv=0,
              units=None):
    """Aggregate arc by host and 5m time bucket via Django ORM.

    For each host: mean of per-bucket summed arc (after dropping the first bucket
    per host). Returns the **arithmetic mean of those per-host values** across
    hosts (all ``avg_*`` simple metrics use this path).

        """
    import pandas as pd
    from hpcperfstats.site.machine.models import host_data

    if not getattr(jt, "_base_filter", None):
      return None
    base = jt._base_filter
    hosts = base.get("host__in") or []
    if not hosts:
      return None
    # Fetch raw samples via ORM.
    qs = (
        host_data.objects.filter(
            time__gte=base["time__gte"],
            time__lte=base["time__lte"],
            host__in=list(hosts),
            type=typename,
            event__in=list(events or []),
        )
        .values("host", "time", "arc")
        .order_by("host", "time")
    )
    rows = list(qs)
    if not rows:
      return None
    df = pd.DataFrame(rows)
    if df.empty:
      return None
    # Floor timestamps to 5‑minute buckets.
    df["time"] = pd.to_datetime(df["time"])
    df["bucket"] = df["time"].dt.floor("5min")
    grouped = (
        df.groupby(["host", "bucket"], as_index=False)["arc"].sum().rename(
            columns={"bucket": "time", "arc": "sum"}
        )
    )
    grouped["sum"] = grouped["sum"] * conv
    if grouped.empty or "host" not in grouped.columns:
      return None
    # Drop first time sample per host to match original behaviour.
    grouped = grouped.sort_values(["host", "time"])
    first_idx = grouped.groupby("host", group_keys=False).head(1).index
    grouped = grouped.drop(index=first_idx)
    if grouped.empty:
      return None
    per_host_vals = grouped.groupby("host")["sum"].mean()
    return float(per_host_vals.mean())

  def _job_arc_avg_flops(self, jt):
    """GFLOP/s from amd64_pmc FLOPS, else Intel FP_ARITH sum, else legacy SSE/AVX double proxies.

    Returns (mean_gf, typename_used) or (None, None).
    """
    v = self.job_arc(
        jt,
        typename="amd64_pmc",
        events=["FLOPS"],
        conv=1e-9,
        units="GF",
    )
    if v is not None:
      return v, "amd64_pmc"
    for intel_typ in ("intel_8pmc3", "intel_4pmc3"):
      v = self.job_arc(
          jt,
          typename=intel_typ,
          events=_INTEL_FP_ARITH_EVENTS,
          conv=1e-9,
          units="GF",
      )
      if v is not None:
        return v, intel_typ
    for intel_typ in ("intel_8pmc3", "intel_4pmc3"):
      total = None
      for ev, weight in _INTEL_LEGACY_SSE_FLOP_EVENTS:
        part = self.job_arc(
            jt,
            typename=intel_typ,
            events=[ev],
            conv=1e-9 * weight,
            units="GF",
        )
        if part is not None:
          total = part if total is None else total + part
      if total is not None and total > 0:
        return total, intel_typ
    return None, None

  # Compute metric
  def compute_metrics(self, job):
    """Compute metrics for one job; return dict with rows (metrics_data-shaped dicts) and distinct_time_count.

        distinct_time_count is the sum over hosts of COUNT(DISTINCT time) in
        jid_table._host_data_qs() for this job (not the global distinct time count).
        """
    metric_compute_start = time.time()

    results = []

    # Job-scoped host_data via ORM (no temp table)
    with jid_table.jid_table(job.jid) as jt:

      job_view = _JobForMetrics(jt)
      distinct_time_count = job_view.per_host_distinct_time_sum

      if job_view.times.size == 0:
        for entry in job_metrics_catalog_entries():
          results.append({
              "jid": job,
              "type": entry["type"],
              "metric": entry["metric"],
              "units": entry["units"],
              "value": None,
              "no_data_reason": NO_TIME_SERIES_MSG,
          })
        log_print("compute metrics time: {0:.1f}".format(time.time() -
                                                     metric_compute_start))
        return {
            "rows": results,
            "distinct_time_count": distinct_time_count,
        }

      for metric_name, metric_obj in self.simple_metrics_list.items():
        if metric_name == "avg_flops":
          value, flops_typename = self._job_arc_avg_flops(jt)
          row_type = flops_typename or metric_obj["typename"]
        else:
          value = self.job_arc(jt, **metric_obj)
          row_type = metric_obj["typename"]

        if value is None:
          results.append({
              "jid": job,
              "type": row_type,
              "metric": metric_name,
              "units": metric_obj["units"],
              "value": None,
              "no_data_reason": NO_SIMPLE_SAMPLES_MSG,
          })
        else:
          results.append({
              "jid": job,
              "type": row_type,
              "metric": metric_name,
              "units": metric_obj["units"],
              "value": value,
              "no_data_reason": None,
          })

      u = utils(job_view)

      for metric_name in self.complex_metrics_list:
        value, typename, units = getattr(sys.modules[__name__],
                                         metric_name)().compute_metric(u)

        if value is None:
          reason = _COMPLEX_NO_DATA_REASONS.get(
              metric_name, "Insufficient data to compute this metric")
          results.append({
              "jid": job,
              "type": typename,
              "metric": metric_name,
              "units": units,
              "value": None,
              "no_data_reason": reason,
          })
        else:
          results.append({
              "jid": job,
              "type": typename,
              "metric": metric_name,
              "units": units,
              "value": value,
              "no_data_reason": None,
          })

    log_print("compute metrics time: {0:.1f}".format(time.time() -
                                                 metric_compute_start))
    return {
        "rows": results,
        "distinct_time_count": distinct_time_count,
    }


def job_metrics_catalog_entries():
  """Ordered catalog of every job-level metric for UI and completeness checks."""
  m = Metrics()
  missing = set(m.complex_metrics_list) - set(_COMPLEX_PLACEHOLDER_TYPE_UNITS)
  if missing:
    raise RuntimeError(
        "complex_metrics_list keys missing from _COMPLEX_PLACEHOLDER_TYPE_UNITS: "
        + ", ".join(sorted(missing))
    )
  out = []
  for metric, spec in m.simple_metrics_list.items():
    out.append({
        "type": spec["typename"],
        "metric": metric,
        "units": spec["units"],
    })
  for name in m.complex_metrics_list:
    t, u = _COMPLEX_PLACEHOLDER_TYPE_UNITS[name]
    out.append({"type": t, "metric": name, "units": u})
  return out


def expected_job_metric_row_count():
  return len(job_metrics_catalog_entries())


def build_job_metrics_display_list(job):
  """API: full metrics_list with a row per catalog metric (value or no_data_reason)."""
  by_metric = {o.metric: o for o in job.metrics_data_set.all()}
  out = []
  for spec in job_metrics_catalog_entries():
    row = by_metric.get(spec["metric"])
    if row is None:
      out.append({
          "type": spec["type"],
          "metric": spec["metric"],
          "units": spec["units"],
          "value": None,
          "no_data_reason": METRIC_NOT_COMPUTED_YET,
      })
    else:
      out.append({
          "type": row.type,
          "metric": row.metric,
          "units": row.units,
          "value": row.value,
          "no_data_reason": row.no_data_reason,
      })
  # Job detail UI: show catalog metrics with values or other reasons first; missing rows last.
  out.sort(key=lambda r: r.get("no_data_reason") == METRIC_NOT_COMPUTED_YET)
  return out


###########
# Complex Metrics #
###########


class avg_freq():
  """Average CPU frequency (GHz) from PMC.

  Uses CLOCKS_UNHALTED_CORE/CLOCKS_UNHALTED_REF when present; otherwise APERF/MPERF
  with the same nominal reference scaling as Intel (u.freq * APERF/MPERF).
    """

  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, 'GHz'
    events = frozenset(schema.events)
    per_host = []

    if "CLOCKS_UNHALTED_CORE" in events and "CLOCKS_UNHALTED_REF" in events:
      ci = schema["CLOCKS_UNHALTED_CORE"].index
      ri = schema["CLOCKS_UNHALTED_REF"].index
      for hostname, stats in _stats.items():
        dc = stats[-1, ci] - stats[0, ci]
        dr = stats[-1, ri] - stats[0, ri]
        if dr == 0:
          continue
        per_host.append(u.freq * dc / dr)
    elif "APERF" in events and "MPERF" in events:
      if u.freq is None:
        return None, typename, 'GHz'
      ai = schema["APERF"].index
      mi = schema["MPERF"].index
      for hostname, stats in _stats.items():
        da = stats[-1, ai] - stats[0, ai]
        dm = stats[-1, mi] - stats[0, mi]
        if dm == 0:
          continue
        per_host.append(u.freq * da / dm)
    else:
      return None, typename, 'GHz'

    if not per_host:
      return None, typename, 'GHz'
    value = float(mean(per_host))
    return value, typename, 'GHz'


class avg_ethbw():
  """Average Ethernet bandwidth (MB/s) from net rx_bytes/tx_bytes.

    """

  def compute_metric(self, u):
    typename = "net"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, 'MB/s'
    rxi = schema["rx_bytes"].index
    txi = schema["tx_bytes"].index
    denom = u.dt * 1024 * 1024
    if denom == 0:
      return None, typename, 'MB/s'
    per_host = []
    for hostname, stats in _stats.items():
      b = (
          stats[-1, rxi] - stats[0, rxi] + stats[-1, txi] - stats[0, txi]
      )
      per_host.append(b / denom)
    if not per_host:
      return None, typename, 'MB/s'
    value = float(mean(per_host))
    if value == 0:
      return None, typename, 'MB/s'
    return value, typename, 'MB/s'


class avg_gpuutil():
  """Average GPU utilization (%) from nvidia_gpu utilization.

    """

  def compute_metric(self, u):
    typename = "nvidia_gpu"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, '%'
    ui = schema["utilization"].index
    per_host = []
    for hostname, stats in _stats.items():
      per_host.append(float(mean(stats[1:-1, ui])))
    if not per_host:
      return None, typename, '%'
    value = float(mean(per_host))
    if value == 0:
      return None, typename, '%'
    return value, typename, '%'


class avg_packetsize():
  """Average packet size (MB) from ib_ext or opa port xmit/rcv data and packets.

    """

  def compute_metric(self, u):
    try:
      typename = "ib_ext"
      schema, _stats = u.get_type(typename)
      if schema is None:
        return None, typename, 'MB'
      tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
      tb, rb = schema["port_xmit_data"].index, schema["port_rcv_data"].index
      conv2mb = 1024 * 1024
    except Exception:
      typename = "opa"
      schema, _stats = u.get_type(typename)
      if schema is None:
        return None, typename, 'MB'
      tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index
      tb, rb = schema["PortXmitData"].index, schema["PortRcvData"].index
      conv2mb = 125000

    per_host = []
    for hostname, stats in _stats.items():
      npk = (
          stats[-1, tx] + stats[-1, rx] - stats[0, tx] - stats[0, rx]
      )
      if npk == 0:
        continue
      nb = (
          stats[-1, tb] + stats[-1, rb] - stats[0, tb] - stats[0, rb]
      )
      per_host.append(nb / (npk * conv2mb))
    if not per_host:
      return None, typename, 'MB'
    value = float(mean(per_host))
    return value, typename, 'MB'


class max_fabricbw():
  """Maximum fabric bandwidth (MB/s) from ib_ext or opa port data.

    """

  def compute_metric(self, u):
    max_bw = 0
    try:
      typename = "ib_ext"
      schema, _stats = u.get_type(typename)
      if schema is None:
        return None, typename, 'MB'
      tx, rx = schema["port_xmit_data"].index, schema["port_rcv_data"].index
      conv2mb = 1024 * 1024
    except Exception:
      typename = "opa"
      schema, _stats = u.get_type(typename)
      if schema is None:
        return None, typename, 'MB'
      tx, rx = schema["PortXmitData"].index, schema["PortRcvData"].index
      conv2mb = 125000
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [tx, rx], conv2mb)
    if cluster_peak is not None:
      return cluster_peak, typename, 'MB/s'
    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(stats[:, tx] + stats[:, rx], u.t)
      fin = ratio[np.isfinite(ratio)]
      if fin.size > 0:
        max_bw = max(max_bw, fin.max())
    if max_bw == 0:
      return None, typename, 'MB/s'
    value = max_bw / conv2mb
    return value, typename, 'MB/s'


class max_lnetbw():
  """Maximum LNET bandwidth (MB/s) from lnet tx_bytes/rx_bytes.

    """

  def compute_metric(self, u):
    typename = "lnet"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, 'MB/s'
    max_bw = 0.0
    tx, rx = schema["tx_bytes"].index, schema["rx_bytes"].index
    div = 1024 * 1024
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [tx, rx], div)
    if cluster_peak is not None:
      return cluster_peak, typename, 'MB/s'
    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(stats[:, tx] + stats[:, rx], u.t)
      fin = ratio[np.isfinite(ratio)]
      if fin.size > 0:
        max_bw = max(max_bw, fin.max())
    if max_bw == 0:
      return None, typename, 'MB/s'
    value = max_bw / div
    return value, typename, 'MB/s'


class max_mds():
  """Maximum Lustre MDS operations (iops) from llite open/close/mmap/fsync/... events.

    """

  def compute_metric(self, u):
    max_mds = 0
    typename = "llite"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, 'iops'
    mds_cols = [
        "open", "close", "mmap", "fsync", "setattr", "truncate", "flock",
        "getattr", "statfs", "alloc_inode", "setxattr", "listxattr",
        "removexattr", "readdir", "create", "lookup", "link", "unlink",
        "symlink", "mkdir", "rmdir", "mknod", "rename",
    ]
    col_idx = [schema[c].index for c in mds_cols]
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, col_idx, 1)
    if cluster_peak is not None:
      return cluster_peak, typename, 'iops'
    for hostname, stats in _stats.items():
      mds_sum = (
          stats[:, schema["open"].index] +
          stats[:, schema["close"].index] +
          stats[:, schema["mmap"].index] +
          stats[:, schema["fsync"].index] +
          stats[:, schema["setattr"].index] +
          stats[:, schema["truncate"].index] +
          stats[:, schema["flock"].index] +
          stats[:, schema["getattr"].index] +
          stats[:, schema["statfs"].index] +
          stats[:, schema["alloc_inode"].index] +
          stats[:, schema["setxattr"].index] +
          stats[:, schema["listxattr"].index] +
          stats[:, schema["removexattr"].index] +
          stats[:, schema["readdir"].index] +
          stats[:, schema["create"].index] +
          stats[:, schema["lookup"].index] +
          stats[:, schema["link"].index] +
          stats[:, schema["unlink"].index] +
          stats[:, schema["symlink"].index] +
          stats[:, schema["mkdir"].index] +
          stats[:, schema["rmdir"].index] +
          stats[:, schema["mknod"].index] +
          stats[:, schema["rename"].index])
      mds_diff = _per_interval_rate(mds_sum, u.t)
      fin = mds_diff[np.isfinite(mds_diff)]
      if fin.size > 0:
        max_mds = max(max_mds, fin.max())
    if max_mds == 0:
      return None, typename, 'iops'
    value = max_mds
    return value, typename, 'iops'


class max_packetrate():
  """Maximum packet rate (#/s) from ib_ext or opa port xmit/rcv packets.

    """

  def compute_metric(self, u):
    max_pr = 0
    try:
      typename = "ib_ext"
      schema, _stats = u.get_type(typename)
      if schema is None:
        return None, typename, '#/s'
      tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
    except Exception:
      typename = "opa"
      schema, _stats = u.get_type(typename)
      if schema is None:
        return None, typename, '#/s'
      tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index

    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [tx, rx], 1)
    if cluster_peak is not None:
      return cluster_peak, typename, '#/s'

    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(stats[:, tx] + stats[:, rx], u.t)
      fin = ratio[np.isfinite(ratio)]
      if fin.size > 0:
        max_pr = max(max_pr, fin.max())
    if max_pr == 0:
      return None, typename, '#/s'
    value = max_pr
    return value, typename, '#/s'


# This will compute the maximum memory usage recorded
# by monitor.  It only samples at x mn intervals and
# may miss high water marks in between.
class mem_hwm():
  """Memory high-water mark (GiB) from mem MemUsed - Slab - FilePages.

    """

  def compute_metric(self, u):
    # mem usage in GB
    max_memusage = 0.0
    typename = "mem"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, 'GiB'
    for hostname, stats in _stats.items():
      mem_arr = (stats[:, schema["MemUsed"].index] -
                 stats[:, schema["Slab"].index] -
                 stats[:, schema["FilePages"].index])
      if mem_arr.size > 0:
        max_memusage = max(max_memusage, amax(mem_arr))
    if max_memusage == 0:
      return None, typename, 'GiB'
    value = max_memusage / (2.**30)
    return value, typename, 'GiB'


class node_imbalance():
  """CPU node imbalance (%): max deviation of per-node CPU rate from max rate.

    """

  def compute_metric(self, u):
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, '%'
    user_i = schema["user"].index
    max_usage = zeros(u.nt - 1)
    for hostname, stats in _stats.items():
      rate = _per_interval_rate(stats[:, user_i], u.t)
      max_usage = maximum(max_usage, np.nan_to_num(rate, nan=-np.inf))

    max_imbalance = []
    for hostname, stats in _stats.items():
      rate = _per_interval_rate(stats[:, user_i], u.t)
      valid = (max_usage > 0) & np.isfinite(rate)
      if np.any(valid):
        rel = (max_usage[valid] - rate[valid]) / max_usage[valid]
        max_imbalance += [mean(rel)]
      else:
        max_imbalance += [float("nan")]
    if max_imbalance == []:
      return None, typename, '%'
    value = 100 * amax([0. if isnan(x) else x for x in max_imbalance])
    return value, typename, '%'


class time_imbalance():
  """CPU time imbalance (%): minimum ratio of integral after/before a time slice.

    """

  def compute_metric(self, u):
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, '%'
    tmid = (u.t[:-1] + u.t[1:]) / 2.0
    dt = diff(u.t)
    user_i = schema["user"].index
    vals = []
    for hostname, stats in _stats.items():
      rate = _per_interval_rate(stats[:, user_i], u.t)
      rate = np.nan_to_num(rate, nan=0.0)
      # skip first and last two time slices
      for i in [x + 2 for x in range(len(u.t) - 4)]:
        r1 = range(i)
        r2 = [x + i for x in range(len(dt) - i)]
        before_window = tmid[i] - tmid[0]
        after_window = tmid[-1] - tmid[i]
        if before_window <= 0 or after_window <= 0:
          continue
        # integral before time slice
        a = trapz(rate[r1], tmid[r1]) / before_window
        if a == 0 or not np.isfinite(a):
          continue
        # integral after time slice
        b = trapz(rate[r2], tmid[r2]) / after_window
        if not np.isfinite(b):
          continue
        # ratio of integral after time over before time
        vals += [b / a]
    if vals:
      value = 100 * min(vals)
      return value, typename, '%'
    else:
      return None, typename, '%'


class vecpercent_64b():
  """Percentage of 64b vectorized FLOPs vs total (from PMC events).

    """

  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, '#'
    vector_widths = {
        "SSE_D_ALL": 1,
        "SIMD_D_256": 2,
        "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE": 1,
        "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE": 2,
        "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE": 4,
        "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE": 8,
        "SSE_DOUBLE_SCALAR": 1,
        "SSE_DOUBLE_PACKED": 2,
        "SIMD_DOUBLE_256": 4
    }
    vector_flops = 0.0
    scalar_flops = 0.0
    for hostname, stats in _stats.items():
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          flops = (stats[-1, index] -
                   stats[0, index]) * vector_widths[eventname]
          if vector_widths[eventname] > 1:
            vector_flops += flops
          else:
            scalar_flops += flops
    denom = scalar_flops + vector_flops
    if denom == 0:
      return None, typename, '#'
    value = 100 * vector_flops / denom
    return value, typename, '%'


class avg_vector_width_64b():
  """Average 64b vector width (FLOPs-weighted) from PMC events.

    """

  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, '#'
    vector_widths = {
        "SSE_D_ALL": 1,
        "SIMD_D_256": 2,
        "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE": 1,
        "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE": 2,
        "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE": 4,
        "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE": 8,
        "SSE_DOUBLE_SCALAR": 1,
        "SSE_DOUBLE_PACKED": 2,
        "SIMD_DOUBLE_256": 4
    }
    per_host = []
    for hostname, stats in _stats.items():
      flops = 0.0
      instr = 0.0
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          instr += (stats[-1, index] - stats[0, index])
          flops += (stats[-1, index] -
                    stats[0, index]) * vector_widths[eventname]
      if instr == 0:
        continue
      per_host.append(flops / instr)
    if not per_host:
      return None, typename, '#'
    value = float(mean(per_host))
    return value, typename, '#'


class vecpercent_32b():
  """Percentage of 32b vectorized FLOPs vs total (from PMC events).

    """

  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, '#'
    vector_widths = {
        "FP_ARITH_INST_RETIRED_SCALAR_SINGLE": 1,
        "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE": 4,
        "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE": 8,
        "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE": 16
    }
    vector_flops = 0.0
    scalar_flops = 0.0
    for hostname, stats in _stats.items():
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          flops = (stats[-1, index] -
                   stats[0, index]) * vector_widths[eventname]
          if vector_widths[eventname] > 1:
            vector_flops += flops
          else:
            scalar_flops += flops
    denom = scalar_flops + vector_flops
    if denom == 0:
      return None, typename, '%'
    value = 100 * vector_flops / denom
    return value, typename, '%'


class avg_vector_width_32b():
  """Average 32b vector width (FLOPs-weighted) from PMC events.

    """

  def compute_metric(self, u):
    typename = "pmc"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, '#'
    vector_widths = {
        "FP_ARITH_INST_RETIRED_SCALAR_SINGLE": 1,
        "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE": 4,
        "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE": 8,
        "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE": 16
    }
    per_host = []
    for hostname, stats in _stats.items():
      flops = 0.0
      instr = 0.0
      for eventname in schema:
        if eventname in vector_widths.keys():
          index = schema[eventname].index
          instr += (stats[-1, index] - stats[0, index])
          flops += (stats[-1, index] -
                    stats[0, index]) * vector_widths[eventname]
      if instr == 0:
        continue
      per_host.append(flops / instr)
    if not per_host:
      return None, typename, '#'
    value = float(mean(per_host))
    return value, typename, '#'
