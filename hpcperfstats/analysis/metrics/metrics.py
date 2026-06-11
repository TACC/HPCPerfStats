"""Metric computation for jobs: simple metrics (job_arc/time_bucket) and complex metrics (avg_freq, avg_ethbw, mem_hwm, etc.) via utils-compatible job view. Results written to metrics_data.

DB access is process-safe: _unwrap runs in multiprocessing workers, calls
connections.close_all() then close_old_connections() so forked children never
reuse the parent's PostgreSQL session (named server-side cursors / iterator
state); writes are done in the main process only.

"""
import json
import os
import hpcperfstats.conf_parser as cfg
from hpcperfstats.print_utils import log_print
from hpcperfstats.process_title import apply_pool_worker_process_title

import multiprocessing
import signal
import sys
import threading
import time
import traceback

import numpy as np
import numexpr as ne
from numpy import amax, diff, isnan, maximum, mean, zeros
from pandas import to_datetime

from django.db import close_old_connections, connections, transaction
from django.db.models import Max, Min
from django.db.utils import OperationalError, DatabaseError

from hpcperfstats.analysis.gen import jid_table
from hpcperfstats.dbload.multiprocessing_pool_health import abort_if_pool_workers_dead
from hpcperfstats.analysis.gen.utils import (
    INTEL_FP_ARITH_ALL_EVENTS,
    INTEL_LEGACY_SSE_FLOP_EVENTS,
    utils,
)
from hpcperfstats.monitor_naming.canonical import (
    HOST_BLOCK_TYPE,
    HOST_CPU_TYPE,
    HOST_IB_TYPE,
    HOST_LNET_TYPE,
    HOST_MEM_TYPE,
    HOST_NFS_TYPE,
    HOST_NUMA_TYPE,
    HOST_OPA_TYPE,
    LUSTRE_LLITE_TYPE,
)
from hpcperfstats.monitor_naming.legacy import (
    LEGACY_HOST_BLOCK_TYPE,
    LEGACY_HOST_CPU_TYPE,
    LEGACY_HOST_LNET_TYPE,
    LEGACY_HOST_MEM_TYPE,
    LEGACY_HOST_NFS_TYPE,
    LEGACY_HOST_NUMA_TYPE,
    LEGACY_HOST_OPA_TYPE,
    LEGACY_LUSTRE_LLITE_TYPE,
)
from hpcperfstats.monitor_naming.resolve import (
    events_probe_names,
    amd_df_type_names,
    amd_pmc_type_names,
    arm_est_flops_event_names,
    arm_imc_types_probe_order,
    core_pmc_types_probe_order,
    dram_cas_read_write_pairs,
    fp_ops_retired_event_names,
    host_cpu_hw_type_names,
    host_cpu_type_names,
    host_mem_type_names,
    host_nfs_type_names,
    imc_types_probe_order,
    resolve_get_type,
    type_probe_names,
)
from hpcperfstats.site.machine.models import host_data, job_data, metrics_data

from hpcperfstats.analysis.metrics.job_detail_fsio import (
    compute_job_detail_fsio_metric_rows,
    fsio_job_detail_catalog,
)
from hpcperfstats.analysis.metrics.db_retry import run_with_db_retry
from hpcperfstats.dbload.db_unavailable import DatabaseUnavailableExit

try:
  from numpy import trapezoid as trapz
except ImportError:
  from numpy import trapz

NUMEXPR_MIN_ARRAY_SIZE = 100_000
METRICS_POOL_JOIN_TIMEOUT_S = max(
    1.0,
    float(os.environ.get("HPCPERFSTATS_METRICS_POOL_JOIN_TIMEOUT_S", "30")),
)
_PARENT_PERSIST_TIMEOUT_MARKERS = (
    "statement timeout",
    "lock timeout",
    "canceling statement due to statement timeout",
    "canceling statement due to lock timeout",
)


class MetricsRunWorkerStallError(TimeoutError):
  """Raised when ``Metrics.run`` makes no worker-result progress for too long."""

  def __init__(
      self,
      stalled_for_s,
      message,
      pool_reset_confirmed=False,
      *,
      partial_outcomes=None,
      pending_jobs=None,
  ):
    super().__init__(message)
    self.stalled_for_s = float(stalled_for_s)
    self.pool_reset_confirmed = bool(pool_reset_confirmed)
    self.partial_outcomes = list(partial_outcomes or [])
    self.pending_jobs = list(pending_jobs or [])


class MetricsComputeJobTimeoutError(TimeoutError):
  """One metrics worker exceeded the per-job compute wall clock."""


def _metrics_jid_value(job_or_pk):
  """Stable jid string from either a job-like object or a raw primary key."""
  return _coerce_metrics_identity_str(getattr(job_or_pk, "jid", job_or_pk))


def _metrics_run_outcome(
    jid,
    *,
    ok,
    status,
    persisted_rows=0,
    distinct_time_count=None,
    persist_s=0.0,
    error_type=None,
    error_message=None,
):
  """Canonical per-jid outcome emitted by ``Metrics.run``."""
  return {
      "jid": _metrics_jid_value(jid),
      "ok": bool(ok),
      "status": str(status),
      "persisted_rows": int(max(0, persisted_rows)),
      "distinct_time_count": distinct_time_count,
      "persist_s": float(max(0.0, persist_s)),
      "error_type": error_type,
      "error_message": error_message,
  }


def _is_parent_persist_timeout_error(exc):
  """Best-effort classification for bounded persist timeout/lock timeout failures."""
  text = str(exc or "").lower()
  return any(marker in text for marker in _PARENT_PERSIST_TIMEOUT_MARKERS)


def _wait_pool_processes_bounded(active_pool, timeout_s):
  """Wait up to ``timeout_s`` for pool worker processes to exit."""
  workers = list(getattr(active_pool, "_pool", []) or [])
  deadline = time.monotonic() + max(0.1, float(timeout_s))
  for proc in workers:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      break
    try:
      proc.join(timeout=remaining)
    except Exception:
      continue
  alive = [getattr(p, "pid", None) for p in workers if getattr(p, "is_alive", lambda: False)()]
  return len(alive) == 0, alive


def _close_pool_bounded(active_pool, timeout_s):
  """Best-effort bounded graceful close; terminates if workers linger."""
  try:
    active_pool.close()
  except Exception:
    pass
  all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
  if all_done:
    return True
  try:
    active_pool.terminate()
  except Exception:
    pass
  all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
  if not all_done:
    log_print(
        "Metrics.run: pool close timeout after terminate; lingering_workers=%s" % alive,
        flush=True,
    )
  return all_done


def _terminate_pool_bounded(active_pool, timeout_s):
  """Best-effort bounded terminate used in stall recovery paths."""
  try:
    active_pool.terminate()
  except Exception:
    pass
  all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
  if not all_done:
    log_print(
        "Metrics.run: pool terminate timeout; lingering_workers=%s" % alive,
        flush=True,
    )
  return all_done


def _log_exception_details(prefix, exc):
  """Emit type/repr and traceback lines for diagnostics-first scheduler logs."""
  et = type(exc).__name__
  log_print(
      "{0}: exception_type={1} exception_repr={2!r}".format(prefix, et, exc),
      flush=True,
  )
  tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
  for raw in tb_lines:
    for line in str(raw).splitlines():
      if line.strip():
        log_print("{0}: traceback {1}".format(prefix, line), flush=True)
  cause = getattr(exc, "__cause__", None)
  if cause is not None:
    log_print(
        "{0}: cause_type={1} cause_repr={2!r}".format(
            prefix, type(cause).__name__, cause
        ),
        flush=True,
    )
  context = getattr(exc, "__context__", None)
  if context is not None and context is not cause:
    log_print(
        "{0}: context_type={1} context_repr={2!r}".format(
            prefix, type(context).__name__, context
        ),
        flush=True,
    )


def _coerce_metrics_identity_str(value):
  """Stable string for metrics_data keys and set/hash uses (never lists/dicts raw).

  Bad monitor/ingest payloads occasionally surface list-typed labels in host_data
  or schema-derived paths; using those in ``set`` membership, ``frozenset``, or
  ORM dedupe keys raises ``unhashable type: 'list'``.
  """
  if value is None:
    return ""
  if isinstance(value, str):
    return value
  if isinstance(value, (list, tuple, set)):
    return ",".join(str(v) for v in value)
  if isinstance(value, dict):
    try:
      return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
      return str(value)
  return str(value)


def _hashable_metric_events_signature(events):
  """Tuple of stable strings for ``simple_metric_cache`` / ``rows_cache`` dict keys.

  ``tuple(events)`` is unsafe when ingest/catalog corruption nests lists inside
  ``events`` — the tuple can contain a raw ``list``, which is unhashable and
  crashes ``cache_key in cache`` during ``job_arc`` / ``job_value_mean``.
  """
  if not events:
    return ()
  return tuple(_coerce_metrics_identity_str(e) for e in events)


def _flatten_event_names_for_host_data_query(events):
  """Expand nested sequences and legacy event aliases for ``event__in`` queries."""
  return events_probe_names(events)


def _sanitize_metrics_compute_rows(rows):
  """Normalize type/metric/units on every worker-produced row before persist."""
  out = []
  for row in rows:
    if not isinstance(row, dict):
      continue
    jid = row.get("jid")
    if jid is None:
      continue
    out.append({
        "jid": jid,
        "type": _coerce_metrics_identity_str(row.get("type")),
        "metric": _coerce_metrics_identity_str(row.get("metric")),
        "units": _coerce_metrics_identity_str(row.get("units")),
        "value": row.get("value"),
        "no_data_reason": row.get("no_data_reason"),
    })
  return out


def _finite_amax(values):
  """Return ``amax`` over finite entries, or ``None`` when none are finite."""
  arr = np.asarray(values, dtype=np.float64)
  if arr.size == 0:
    return None
  fin = arr[np.isfinite(arr)]
  if fin.size == 0:
    return None
  return float(amax(fin))


def _resolve_metrics_run_per_job_timeout_s():
  """Per-job compute wall clock in pool workers (0 INI/env → ``metrics_run_stall_timeout_s``)."""
  per_job = float(cfg.get_metrics_run_per_job_timeout_s())
  if per_job > 0.0:
    return per_job
  return max(5.0, float(cfg.get_metrics_run_stall_timeout_s()))


def _run_compute_metrics_timed(metrics_obj, job, timeout_s):
  """Run ``compute_metrics`` with a Unix wall-clock cap (no-op when ``timeout_s <= 0``)."""
  timeout_s = float(timeout_s)
  if timeout_s <= 0.0:
    return metrics_obj.compute_metrics(job)
  if not hasattr(signal, "SIGALRM"):
    return metrics_obj.compute_metrics(job)

  jid = getattr(job, "jid", "?")

  def _handler(signum, frame):
    del signum, frame
    raise MetricsComputeJobTimeoutError(
        "compute_metrics exceeded {:.0f}s for jid {}".format(timeout_s, jid))

  previous = signal.getsignal(signal.SIGALRM)
  signal.signal(signal.SIGALRM, _handler)
  try:
    if hasattr(signal, "setitimer"):
      signal.setitimer(signal.ITIMER_REAL, timeout_s)
    else:
      signal.alarm(max(1, int(timeout_s)))
    return metrics_obj.compute_metrics(job)
  finally:
    if hasattr(signal, "setitimer"):
      signal.setitimer(signal.ITIMER_REAL, 0)
    else:
      signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


def _coerced_metric_name_set(metric_names):
  """Return a hash-safe set of metric names from any iterable."""
  out = set()
  for name in metric_names or ():
    out.add(_coerce_metrics_identity_str(name))
  return out


# Skip time_imbalance slices where b/a is non-finite or absurd (near-zero "before"
# integral blows up the ratio); values above this are not meaningful as %.
_TIME_IMBALANCE_MAX_SLICE_RATIO = 1e9


def _add_arrays(a, b):
  """Fast path for a+b on large arrays."""
  if getattr(a, "size", 0) >= NUMEXPR_MIN_ARRAY_SIZE:
    return ne.evaluate("a + b")
  return a + b

# Default (type, units) for complex metrics when building the catalog / no-time-series rows.
# Types match the primary telemetry source for each metric (see compute_metric classes).
_COMPLEX_PLACEHOLDER_TYPE_UNITS = {
    "avg_freq": ("pmc", "GHz"),
    "avg_ethbw": ("net", "MB/s"),
    "avg_gpuutil": ("gpu", "%"),
    "avg_packetsize": (HOST_IB_TYPE, "MB"),
    "max_fabricbw": (HOST_IB_TYPE, "MB/s"),
    "max_lnetbw": (HOST_LNET_TYPE, "MB/s"),
    "max_mds": (LUSTRE_LLITE_TYPE, "iops"),
    "max_packetrate": (HOST_IB_TYPE, "#/s"),
    "max_opa_congestion_rate": (HOST_OPA_TYPE, "#/s"),
    "max_numa_remote_rate": (HOST_NUMA_TYPE, "#/s"),
    "flops_node_imbalance": ("pmc", "%"),
    "fabric_node_imbalance": (HOST_IB_TYPE, "%"),
    "dram_bw_node_imbalance": ("imc", "%"),
    "lnet_node_imbalance": ("lnet", "%"),
    "avg_tensor_active": ("nvidia_gpu", "%"),
    "avg_fp16_active": ("nvidia_gpu", "%"),
    "avg_fp32_active": ("nvidia_gpu", "%"),
    "avg_fp64_active": ("nvidia_gpu", "%"),
    "avg_gpu_mem_bw_gbps": ("nvidia_gpu", "GB/s"),
    "max_gpu_power": ("nvidia_gpu", "W"),
    "max_node_power_est_w": ("job", "W"),
    "avg_node_power_est_w": ("job", "W"),
    "max_gpu_link_gbps": ("nvidia_gpu", "GB/s"),
    "max_gpu_clock_event_reasons": ("nvidia_gpu", "#"),
    "gpu_util_node_imbalance": ("nvidia_gpu", "%"),
    "tensor_node_imbalance": ("nvidia_gpu", "%"),
    "avg_fabric_mb_per_avg_tensor": (HOST_IB_TYPE, "MB/s"),
    "mem_hwm": (HOST_MEM_TYPE, "GiB"),
    "node_imbalance": (HOST_CPU_TYPE, "%"),
    "time_imbalance": (HOST_CPU_TYPE, "%"),
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
    "max_mds": "No usable Lustre/NFS telemetry for metadata/operation rate",
    "max_packetrate": "No usable fabric telemetry for peak packet rate",
    "max_opa_congestion_rate": "No usable OPA congestion telemetry",
    "max_numa_remote_rate": "No usable NUMA remote-access telemetry",
    "flops_node_imbalance": "No usable FLOPs telemetry for node imbalance",
    "fabric_node_imbalance": "No usable fabric telemetry for node imbalance",
    "dram_bw_node_imbalance": "No usable DRAM bandwidth telemetry for node imbalance",
    "lnet_node_imbalance": "No usable LNET byte telemetry for node imbalance",
    "avg_tensor_active": "No usable GPU tensor-activity telemetry",
    "avg_fp16_active": "No usable GPU FP16-activity telemetry",
    "avg_fp32_active": "No usable GPU FP32-activity telemetry",
    "avg_fp64_active": "No usable GPU FP64-activity telemetry",
    "avg_gpu_mem_bw_gbps": "No usable GPU memory bandwidth rate telemetry",
    "max_gpu_power": "No usable GPU power telemetry",
    "max_node_power_est_w": "No usable node power estimate telemetry",
    "avg_node_power_est_w": "No usable node power estimate telemetry",
    "max_gpu_link_gbps": "No usable GPU PCIe/NVLink byte telemetry",
    "max_gpu_clock_event_reasons": "No usable GPU clock event reason telemetry",
    "gpu_util_node_imbalance": "No usable GPU utilization telemetry for imbalance",
    "tensor_node_imbalance": "No usable GPU tensor telemetry for imbalance",
    "avg_fabric_mb_per_avg_tensor": "No usable fabric and tensor telemetry for ratio",
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
INSUFFICIENT_DATA_FOR_METRICS_PROCESSING = "Insufficient Data For Metrics Processing"

# Persisted with ``compute_metrics`` (ORM GPU aggregates; same definition as job_detail).
_GPU_JOB_DETAIL_CATALOG = (
    ("detail_gpu_active", "gpu", "count"),
    ("detail_gpu_util_max", "gpu", "%"),
    ("detail_gpu_util_mean", "gpu", "%"),
    ("detail_gpu_count", "gpu", "count"),
)

NO_GPU_AGGREGATE_TELEMETRY = "No usable GPU aggregate telemetry for job detail"


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

  def __contains__(self, name):
    """Membership check for event columns (partial schemas must not KeyError complex metrics)."""
    return str(name) in self._index

  def __iter__(self):
    """Iterate event names (required: without this, ``for x in schema`` uses integer indices and breaks __getitem__)."""
    return iter(self.events)


def _schema_has_events(schema, *event_names):
  """True when ``schema`` defines every listed event (handles incomplete ``mem``/fabric/net rows)."""
  if schema is None:
    return False
  return all(name in schema for name in event_names)


class _Host:
  """Minimal host container with a stats dict (typename -> dev -> array).

    """

  def __init__(self):
    """Initialize empty stats dict."""
    self.stats = {}


def _fqdn_hosts_for_job_model(job):
  suffix = "." + cfg.get_host_name_ext()
  hosts = []
  for host in (job.host_list or []):
    h = str(host or "").strip()
    if not h:
      continue
    hosts.append(h if "." in h else (h + suffix))
  return hosts


def _in_window_telemetry_bounds_for_job(job):
  """Return ``(telemetry_first_time, telemetry_last_time)`` for accounting hosts."""
  start_time = getattr(job, "start_time", None)
  end_time = getattr(job, "end_time", None)
  host_list = getattr(job, "host_list", None)
  if start_time is None or end_time is None or host_list is None:
    row = (
        job_data.objects.filter(jid=_metrics_jid_value(job))
        .values("start_time", "end_time", "host_list")
        .first()
    )
    if not row:
      return None, None
    start_time = row.get("start_time")
    end_time = row.get("end_time")
    host_list = row.get("host_list")
  suffix = "." + cfg.get_host_name_ext()
  hosts = []
  for host in (host_list or []):
    h = str(host or "").strip()
    if not h:
      continue
    hosts.append(h if "." in h else (h + suffix))
  if not hosts or start_time is None or end_time is None:
    return None, None
  agg = (
      host_data.objects.filter(
          host__in=hosts,
          time__gte=start_time,
          time__lte=end_time,
      )
      .aggregate(mn=Min("time"), mx=Max("time"))
  )
  return agg.get("mn"), agg.get("mx")


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

    # Global sorted time axis.
    df["time"] = to_datetime(df["time"]).dt.tz_localize(None)
    df = df.sort_values("time")
    for col in ("host", "type", "event"):
      if col in df.columns:
        df[col] = df[col].map(_coerce_metrics_identity_str)
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
    for raw_typename, events in jt.schema.items():
      typename = _coerce_metrics_identity_str(raw_typename)
      ev_list = [_coerce_metrics_identity_str(e) for e in (events or [])]
      self.schemas[typename] = _Schema(ev_list)

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
      pavg = type_df[["time", "event", "value"]]
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
            index=times_index, fill_value=np.nan
        ).reindex(columns=events, fill_value=np.nan)
        stats = np.ascontiguousarray(pivot.values, dtype=np.float64)
        host_obj.stats.setdefault(typename, {})
        host_obj.stats[typename]["agg"] = stats
      del type_df


def _unwrap(args):
  """Wrapper for pool: call compute_metrics on the job. Used by Metrics.run.

    """
  # Fork inherits the parent's DB socket on Linux; Django named cursors (iterator)
  # must never share one PostgreSQL session across processes (#close_all_after_fork).
  connections.close_all()
  close_old_connections()
  metrics_obj, job = args

  # Lost DB connections in worker processes can manifest as "lost
  # synchronization with server" DatabaseErrors. Retry once with a clean
  # connection; on repeated failure, log and skip this job rather than
  # crashing the entire pool.
  try:
    per_job_timeout_s = _resolve_metrics_run_per_job_timeout_s()
    payload = run_with_db_retry(
        lambda: _run_compute_metrics_timed(
            metrics_obj, job, per_job_timeout_s),
        attempts=2,
    )
    if not isinstance(payload, dict):
      payload = {}
    return {
        "jid": _metrics_jid_value(job),
        "status": "ok",
        "rows": payload.get("rows") or [],
        "distinct_time_count": payload.get("distinct_time_count"),
        "telemetry_first_time": payload.get("telemetry_first_time"),
        "telemetry_last_time": payload.get("telemetry_last_time"),
        "error_type": None,
        "error_message": None,
    }
  except DatabaseUnavailableExit:
    raise
  except (OperationalError, DatabaseError) as exc:
    log_print(
        "Skipping metrics for jid %s after DB error in worker: %s" %
        (getattr(job, "jid", "?"), exc)
    )
    return {
        "jid": _metrics_jid_value(job),
        "status": "worker_db_error",
        "rows": [],
        "distinct_time_count": None,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }
  except Exception as exc:
    _log_exception_details(
        "Skipping metrics for jid {0} after compute error".format(
            getattr(job, "jid", "?")),
        exc,
    )
    return {
        "jid": _metrics_jid_value(job),
        "status": "worker_compute_error",
        "rows": [],
        "distinct_time_count": None,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }


def _persist_metrics_batch(
    job_results,
    distinct_time_count,
    telemetry_first_time=None,
    telemetry_last_time=None,
):
  """Upsert metrics_data rows for job_results; set job_data.metrics_distinct_time_count.

  Uses bulk_create(..., update_conflicts=...) so we do not rely on INSERT RETURNING
  row-count matching (Django asserts that for plain bulk_create on PostgreSQL;
  some stacks violate it). Dedupes (jid, type, metric) within the batch so
  ON CONFLICT does not hit the same row twice.
  Called in main process only.
  """
  conn = connections["default"]
  using = getattr(conn, "alias", None) or "default"
  with transaction.atomic(using=using):
    if conn.vendor == "postgresql":
      try:
        with conn.cursor() as cursor:
          cursor.execute(
              "SET LOCAL statement_timeout = %s",
              [max(1000, int(cfg.get_metrics_persist_statement_timeout_ms()))],
          )
          cursor.execute(
              "SET LOCAL lock_timeout = %s",
              [max(1000, int(cfg.get_metrics_persist_lock_timeout_ms()))],
          )
      except Exception as exc:
        # pytest-django ``django_db(databases=[])`` forbids cursors on the test wrapper.
        from django.test.testcases import DatabaseOperationForbidden

        if not isinstance(exc, DatabaseOperationForbidden):
          raise
    jids = list({_metrics_jid_value(item["jid"]) for item in job_results})
    by_key = {}
    for item in job_results:
      row_jid = _metrics_jid_value(item["jid"])
      row_type = _coerce_metrics_identity_str(item["type"])
      row_metric = _coerce_metrics_identity_str(item["metric"])
      key = (row_jid, row_type, row_metric)
      by_key[key] = item
    rows = [
        metrics_data(
            jid_id=_metrics_jid_value(item["jid"]),
            type=_coerce_metrics_identity_str(item["type"]),
            metric=_coerce_metrics_identity_str(item["metric"]),
            units=_coerce_metrics_identity_str(item["units"]),
            value=item["value"],
            no_data_reason=item.get("no_data_reason"),
        )
        for item in by_key.values()
    ]
    wrote_metrics = bool(rows)
    if rows:
      metrics_data.objects.bulk_create(
          rows,
          update_conflicts=True,
          update_fields=["units", "value", "no_data_reason"],
          unique_fields=["jid", "type", "metric"],
      )
    if distinct_time_count is not None and jids:
      jobs_up = list(job_data.objects.filter(pk__in=jids))
      update_fields = ["metrics_distinct_time_count"]
      for jo in jobs_up:
        jo.metrics_distinct_time_count = distinct_time_count
        if telemetry_first_time is not None:
          jo.telemetry_first_time = telemetry_first_time
        if telemetry_last_time is not None:
          jo.telemetry_last_time = telemetry_last_time
      if telemetry_first_time is not None:
        update_fields.append("telemetry_first_time")
      if telemetry_last_time is not None:
        update_fields.append("telemetry_last_time")
      job_data.objects.bulk_update(jobs_up, update_fields)

  if wrote_metrics:
    try:
      from hpcperfstats.site.machine.cache_utils import invalidate_metrics_distinct_cache

      invalidate_metrics_distinct_cache()
    except Exception:
      pass


def _persist_metrics_payload(payload):
  """Persist one worker payload and return a truthful per-jid outcome."""
  jid = _metrics_jid_value(payload.get("jid"))
  status = str(payload.get("status") or "ok")
  if status != "ok":
    log_print(
        "Metrics.run worker outcome failed jid={0} status={1} error_type={2} error={3!r}".format(
            jid,
            status,
            payload.get("error_type"),
            payload.get("error_message"),
        ),
        flush=True,
    )
    return _metrics_run_outcome(
        jid,
        ok=False,
        status=status,
        persisted_rows=0,
        distinct_time_count=payload.get("distinct_time_count"),
        error_type=payload.get("error_type"),
        error_message=payload.get("error_message"),
    )
  job_rows = payload.get("rows") or []
  distinct_n = payload.get("distinct_time_count")
  telemetry_first_time = payload.get("telemetry_first_time")
  telemetry_last_time = payload.get("telemetry_last_time")
  if not job_rows:
    return _metrics_run_outcome(
        jid,
        ok=True,
        status="ok",
        persisted_rows=0,
        distinct_time_count=distinct_n,
        persist_s=0.0,
    )
  persist_started_at = time.monotonic()
  try:
    run_with_db_retry(
        lambda: _persist_metrics_batch(
            job_rows,
            distinct_n,
            telemetry_first_time=telemetry_first_time,
            telemetry_last_time=telemetry_last_time,
        ),
        attempts=2,
    )
  except (OperationalError, DatabaseError) as exc:
    persist_elapsed = time.monotonic() - persist_started_at
    persist_status = (
        "parent_persist_timeout"
        if _is_parent_persist_timeout_error(exc)
        else "parent_persist_db_error"
    )
    log_print(
        "Metrics.run parent persist failure jid={0} status={1} elapsed_s={2:.3f} "
        "error_type={3} error={4!r}".format(
            jid,
            persist_status,
            persist_elapsed,
            type(exc).__name__,
            exc,
        ),
        flush=True,
    )
    return _metrics_run_outcome(
        jid,
        ok=False,
        status=persist_status,
        persisted_rows=0,
        distinct_time_count=distinct_n,
        persist_s=persist_elapsed,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )
  persist_elapsed = time.monotonic() - persist_started_at
  return _metrics_run_outcome(
      jid,
      ok=True,
      status="ok",
      persisted_rows=len(job_rows),
      distinct_time_count=distinct_n,
      persist_s=persist_elapsed,
  )


def _drain_metrics_imap(
    active_pool,
    tasks,
    chunksize,
    *,
    poll_timeout_s,
    stall_timeout_s,
):
  """Apply ``imap_unordered`` results from workers and persist metrics.

  ``imap_unordered`` can block forever when a worker wedges (driver deadlock,
  query hang, C-extension lock). Poll with timeout and fail fast on prolonged
  no-progress so scheduler code can recover the pool and continue.
  """
  # multiprocessing IMap* timeout polling is only reliable with chunksize=1.
  # Larger chunks can block indefinitely on ``next(timeout=...)`` and bypass
  # stall detection (observed as inflight batches with zero completions).
  submit_chunksize = 1
  abort_if_pool_workers_dead(
      active_pool,
      context="Metrics.run imap_unordered (preflight)",
  )
  iterator = active_pool.imap_unordered(
      _unwrap,
      tasks,
      chunksize=submit_chunksize,
  )
  iterator_next = getattr(iterator, "next", None)
  iterator_next_supports_timeout = callable(iterator_next)
  total = len(tasks)
  done = 0
  last_progress_at = time.monotonic()
  completed_jids = set()
  outcomes = []
  while done < total:
    abort_if_pool_workers_dead(
        active_pool,
        context="Metrics.run imap_unordered",
    )
    try:
      if iterator_next_supports_timeout:
        payload = iterator_next(timeout=float(max(0.0, poll_timeout_s)))
      else:
        # Some pool adapters/tests return plain generators with ``__next__`` only.
        payload = next(iterator)
    except DatabaseUnavailableExit:
      raise
    except multiprocessing.TimeoutError:
      stalled_for = time.monotonic() - last_progress_at
      if stalled_for >= max(0.0, float(stall_timeout_s)):
        pending_jobs = [
            job
            for _metrics_obj, job in tasks
            if _metrics_jid_value(job) not in completed_jids
        ]
        raise MetricsRunWorkerStallError(
            stalled_for_s=stalled_for,
            message=(
                "Metrics.run worker stall: no completed jobs for %.1fs "
                "(tasks=%s chunksize=%s completed=%s pending=%s)"
            )
            % (
                stalled_for,
                total,
                submit_chunksize,
                done,
                len(pending_jobs),
            ),
            pool_reset_confirmed=False,
            partial_outcomes=list(outcomes),
            pending_jobs=pending_jobs,
        )
      continue
    except StopIteration:
      break
    done += 1
    last_progress_at = time.monotonic()
    if not payload:
      outcomes.append(
          _metrics_run_outcome(
              "unknown",
              ok=False,
              status="empty_worker_payload",
              error_type="EmptyPayload",
              error_message="worker returned empty payload",
          )
      )
      continue
    outcomes.append(_persist_metrics_payload(payload))
    completed_jids.add(_metrics_jid_value(payload.get("jid")))
  return outcomes


def _jid_table_host_data_time_kwargs(base):
  """ORM time scope from ``jid_table._base_filter`` (full window or sampled ``time__in``)."""
  if not base:
    return None
  if "time__in" in base:
    return {"time__in": base["time__in"]}
  time_gte = base.get("time__gte")
  time_lte = base.get("time__lte")
  if time_gte is None or time_lte is None:
    return None
  return {"time__gte": time_gte, "time__lte": time_lte}


# Skip row memo when strided ``time__in`` is huge (avoid giant cache keys and RAM).
_HOST_DATA_ROWS_MEMO_MAX_TIME_IN = 4096


def _host_data_row_cache_key(tkw, typename, events, metric_column):
  """Hashable key for one batched host_data fetch within a single ``compute_metrics`` pass."""
  if not tkw:
    return None
  ti = tkw.get("time__in")
  if ti is not None:
    try:
      n = len(ti)
    except TypeError:
      return None
    if n > _HOST_DATA_ROWS_MEMO_MAX_TIME_IN:
      return None
    # Same ``tkw`` dict is reused across metric helpers; ``id`` ties arc vs value passes.
    t_part = ("time__in", id(ti))
  else:
    t_part = ("range", tkw.get("time__gte"), tkw.get("time__lte"))
  return (typename, metric_column, _hashable_metric_events_signature(events), t_part)


def _host_data_metric_rows_batched(
    tkw, hosts, typename, events, metric_column, rows_cache=None):
  """Fetch host_data rows for metrics bucketing; chunk ``host__in`` like jid_table."""
  from hpcperfstats.site.machine.models import host_data

  host_list = list(hosts)
  if not host_list:
    return []
  cache_key = None
  if rows_cache is not None:
    cache_key = _host_data_row_cache_key(tkw, typename, events, metric_column)
    if cache_key is not None and cache_key in rows_cache:
      return rows_cache[cache_key]
  batch = jid_table._coerce_jid_table_host_query_batch_size(
      jid_table.JID_TABLE_HOST_QUERY_BATCH)
  ev = _flatten_event_names_for_host_data_query(events)
  rows = []
  for i in range(0, len(host_list), batch):
    chunk = host_list[i:i + batch]
    qs = (
        host_data.objects.filter(
            **tkw,
            host__in=chunk,
            type=typename,
            event__in=ev,
        )
        .values("host", "time", metric_column)
        .order_by("host", "time")
    )
    for row in qs:
      rows.append(row)
  if rows_cache is not None and cache_key is not None:
    rows_cache[cache_key] = rows
  return rows


class Metrics():
  """Computes simple and complex metrics for a list of jobs in parallel and writes results to metrics_data.

    """

  def __init__(self):
    """Initialize simple_metrics_list and complex_metrics_list.

        """
    self.simple_metrics_list = {
        "avg_blockbw": {
            "typename": HOST_BLOCK_TYPE,
            "events": ["rd_sectors", "wr_sectors"],
            "conv": 1.0 / (1024 * 1024),
            "units": "GB/s",
            "nonnegative_rate": True,
        },
        "avg_cpuusage": {
            "typename": HOST_CPU_TYPE,
            "events": ["user", "system", "nice"],
            "conv": 0.01,
            "units": "#cores"
        },
        "avg_sharedfs_iops": {
            "typename": LUSTRE_LLITE_TYPE,
            "events": [
                "open", "close", "mmap", "fsync", "setattr", "truncate",
                "flock", "getattr", "statfs", "alloc_inode", "setxattr",
                "listxattr", "removexattr", "readdir", "create", "lookup",
                "link", "unlink", "symlink", "mkdir", "rmdir", "mknod", "rename"
            ],
            "conv": 1,
            "units": "iops"
        },
        "avg_sharedfs_bw": {
            "typename": LUSTRE_LLITE_TYPE,
            "events": ["read_bytes", "write_bytes"],
            "conv": 1.0 / (1024 * 1024),
            "units": "MB/s"
        },
        "avg_ibbw": {
            "typename": HOST_IB_TYPE,
            "events": ["port_xmit_data", "port_rcv_data"],
            "conv": 1.0 / (1024 * 1024),
            "units": "MB/s",
            "nonnegative_rate": True,
        },
        "avg_fabric_mb_per_gflops": {
            "typename": HOST_IB_TYPE,
            "events": [],
            "conv": 0.0,
            "units": "MB/GF",
        },
        "avg_tensor_active": {
            "typename": "nvidia_gpu",
            "events": ["tensor_active"],
            "conv": 0.0,
            "units": "%",
        },
        "avg_fp16_active": {
            "typename": "nvidia_gpu",
            "events": ["fp16_active"],
            "conv": 0.0,
            "units": "%",
        },
        "avg_fp32_active": {
            "typename": "nvidia_gpu",
            "events": ["fp32_active"],
            "conv": 0.0,
            "units": "%",
        },
        "avg_fp64_active": {
            "typename": "nvidia_gpu",
            "events": ["fp64_active"],
            "conv": 0.0,
            "units": "%",
        },
        "avg_gpu_mem_bw_gbps": {
            "typename": "nvidia_gpu",
            "events": ["gpu_mem_bw_bytes_rate"],
            "conv": 0.0,
            "units": "GB/s",
        },
        "avg_fabric_mb_per_avg_tensor": {
            "typename": HOST_IB_TYPE,
            "events": [],
            "conv": 0.0,
            "units": "MB/s",
        },
        "avg_flops": {
            "typename": "amd_x86_pmc",
            "events": ["fp_ops_retired"],
            "conv": 1e-9,
            "units": "GF"
        },
        "avg_mbw": {
            "typename": "amd_x86_uncore_df",
            "events": [
                "MBW_CHANNEL_0",
                "MBW_CHANNEL_1",
                "MBW_CHANNEL_2",
                "MBW_CHANNEL_3",
                "MBW_CHANNEL_4",
                "MBW_CHANNEL_5",
                "MBW_CHANNEL_6",
                "MBW_CHANNEL_7",
            ],
            "conv": 2 / (1024 * 1024 * 1024),
            "units": "GB/s"
        }
    }

    self.complex_metrics_list = [
        'avg_freq', 'avg_ethbw', 'avg_packetsize',
        'max_fabricbw', 'max_lnetbw', 'max_mds', 'max_packetrate',
        'max_opa_congestion_rate', 'max_numa_remote_rate',
        'max_gpu_power', 'max_node_power_est_w', 'avg_node_power_est_w',
        'max_gpu_link_gbps', 'max_gpu_clock_event_reasons',
        'mem_hwm',
        'node_imbalance', 'time_imbalance', 'flops_node_imbalance',
        'fabric_node_imbalance', 'dram_bw_node_imbalance', 'lnet_node_imbalance',
        'gpu_util_node_imbalance', 'tensor_node_imbalance',
        'vecpercent_64b',
        'avg_vector_width_64b', 'vecpercent_32b', 'avg_vector_width_32b'
    ]
    self._shared_pool = None
    self._shared_pool_kind = None

  def __getstate__(self):
    """Exclude non-picklable runtime pool when sending self to workers."""
    state = dict(self.__dict__)
    state["_shared_pool"] = None
    state["_shared_pool_kind"] = None
    return state

  def __setstate__(self, state):
    self.__dict__.update(state)
    if "_shared_pool" not in self.__dict__:
      self._shared_pool = None
    if "_shared_pool_kind" not in self.__dict__:
      self._shared_pool_kind = None

  def _worker_process_count(self):
    return cfg.get_metrics_pool_process_count()

  def _imap_chunksize(self, job_count, threads):
    if job_count <= 0:
      return 1
    # Balance IPC overhead and fairness.
    return max(1, job_count // (threads * 4))

  def ensure_pool(self, pool_kind="metrics-pool"):
    """Create and retain a shared worker pool for repeated run() calls."""
    if (
        self._shared_pool is not None
        and self._shared_pool_kind == pool_kind
    ):
      return self._shared_pool
    if self._shared_pool is not None:
      self.close_pool()
    self._shared_pool_kind = pool_kind
    self._shared_pool = multiprocessing.Pool(
        processes=self._worker_process_count(),
        initializer=apply_pool_worker_process_title,
        initargs=("update_metrics.py", pool_kind),
    )
    return self._shared_pool

  def close_pool(self):
    """Close retained worker pool (idempotent)."""
    if self._shared_pool is None:
      return
    _close_pool_bounded(self._shared_pool, METRICS_POOL_JOIN_TIMEOUT_S)
    self._shared_pool = None
    self._shared_pool_kind = None

  def reset_pool_hard(self):
    """Terminate retained worker pool without blocking the scheduler thread.

    Detach the pool reference first so ``ensure_pool()`` can create a fresh pool
    while lingering workers are torn down in the background. Blocking on
    ``terminate()``/``join()`` after a wedged worker caused indefinite stalls
    after ``MetricsRunWorkerStallError`` was logged.
    """
    pool = self._shared_pool
    if pool is None:
      return
    self._shared_pool = None
    self._shared_pool_kind = None

    def _terminate_background():
      try:
        _terminate_pool_bounded(pool, METRICS_POOL_JOIN_TIMEOUT_S)
      except Exception:
        pass

    threading.Thread(
        target=_terminate_background,
        name="metrics-pool-terminate",
        daemon=True,
    ).start()

  # Compute metrics in parallel (Shared memory only)
  def run(self, job_list, pool=None):
    """Run metric computation for each job in job_list in a process pool; persist results via metrics_data.update_or_create.

        """
    if not job_list:
      log_print("Please specify a job list.")
      return []

    threads = self._worker_process_count()
    pool_chunksize = self._imap_chunksize(len(job_list), threads)
    own_pool = pool is None
    active_pool = pool
    if active_pool is None:
      active_pool = multiprocessing.Pool(
          processes=threads,
          initializer=apply_pool_worker_process_title,
          initargs=("update_metrics.py", "metrics-pool"),
      )
    tasks = [(self, job) for job in job_list]
    poll_timeout_s = cfg.get_metrics_run_poll_timeout_s()
    stall_timeout_s = cfg.get_metrics_run_stall_timeout_s()
    outcomes = []
    try:
      try:
        outcomes = _drain_metrics_imap(
            active_pool,
            tasks,
            pool_chunksize,
            poll_timeout_s=poll_timeout_s,
            stall_timeout_s=stall_timeout_s,
        )
      except IndexError:
        # Rare pool/imap edge case (e.g. short batches); single-job tasks avoid it.
        log_print(
            "Metrics.run: imap raised IndexError (batch size=%s); retrying per-job."
            % len(tasks),
            flush=True,
        )
        for single in tasks:
          outcomes.extend(_drain_metrics_imap(
              active_pool,
              [single],
              1,
              poll_timeout_s=poll_timeout_s,
              stall_timeout_s=stall_timeout_s,
          ))
    except MetricsRunWorkerStallError as exc:
      log_print("Metrics.run: %s" % exc, flush=True)
      reset_confirmed = False
      if own_pool:
        try:
          reset_confirmed = _terminate_pool_bounded(
              active_pool,
              METRICS_POOL_JOIN_TIMEOUT_S,
          )
          active_pool = None
        except Exception:
          pass
      else:
        self.reset_pool_hard()
        reset_confirmed = self._shared_pool is None
      outcomes.extend(exc.partial_outcomes)
      for job in exc.pending_jobs:
        outcomes.append(_metrics_run_outcome(
            job,
            ok=False,
            status="worker_stall_timeout",
            error_type="MetricsRunWorkerStallError",
            error_message=str(exc),
        ))
      if outcomes:
        log_print(
            "Metrics.run: recovered after worker stall completed={0} "
            "failed={1} pool_reset_confirmed={2}".format(
                sum(1 for o in outcomes if o.get("ok")),
                sum(1 for o in outcomes if not o.get("ok")),
                1 if reset_confirmed else 0,
            ),
            flush=True,
        )
        return outcomes
      raise MetricsRunWorkerStallError(
          stalled_for_s=exc.stalled_for_s,
          message=str(exc),
          pool_reset_confirmed=reset_confirmed,
          partial_outcomes=exc.partial_outcomes,
          pending_jobs=exc.pending_jobs,
      )
    except Exception as exc:
      _log_exception_details("Metrics.run failure", exc)
      raise
    finally:
      if own_pool and active_pool is not None:
        _close_pool_bounded(active_pool, METRICS_POOL_JOIN_TIMEOUT_S)
    return outcomes

  def job_arc(self,
              jt,
              name=None,
              typename=None,
              events=None,
              conv=0,
              units=None,
              cache=None,
              rows_cache=None,
              nonnegative_rate=False):
    """Aggregate arc by host and 5m time bucket via Django ORM.

    For each host: mean of per-bucket summed arc (after dropping the first bucket
    per host). Returns the **arithmetic mean of those per-host values** across
    hosts (all ``avg_*`` simple metrics use this path).

    When ``nonnegative_rate`` is True, negative ``arc`` samples are dropped (NaN)
    before bucketing. Use for cumulative byte counters (fabric bandwidth) where
    a negative rate indicates reset, wrong rollover width, or bad samples.

        """
    import pandas as pd

    if not getattr(jt, "_base_filter", None):
      return None
    base = jt._base_filter
    hosts = base.get("host__in") or []
    if not hosts:
      return None
    cache_key = None
    if cache is not None:
      cache_key = (
          _coerce_metrics_identity_str(typename),
          _hashable_metric_events_signature(events),
          float(conv),
          bool(nonnegative_rate),
      )
      if cache_key in cache:
        return cache[cache_key]
    tkw = _jid_table_host_data_time_kwargs(base)
    if not tkw:
      return None
    rows = []
    for typ in type_probe_names(typename):
      rows = _host_data_metric_rows_batched(
          tkw, hosts, typ, events, "arc", rows_cache=rows_cache)
      if rows:
        break
    if not rows:
      if cache is not None:
        cache[cache_key] = None
      return None
    df = pd.DataFrame(rows)
    if df.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    if nonnegative_rate:
      df["arc"] = df["arc"].where(df["arc"] >= 0)
    # Floor timestamps to 5‑minute buckets.
    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
      df["time"] = pd.to_datetime(df["time"])
    df["bucket"] = df["time"].dt.floor("5min")
    grouped = (
        df.groupby(["host", "bucket"], as_index=False)["arc"].sum().rename(
            columns={"bucket": "time", "arc": "sum"}
        )
    )
    grouped["sum"] = grouped["sum"] * conv
    if grouped.empty or "host" not in grouped.columns:
      if cache is not None:
        cache[cache_key] = None
      return None
    # Drop first time sample per host to match original behaviour.
    first_idx = grouped.groupby("host", group_keys=False).head(1).index
    grouped = grouped.drop(index=first_idx)
    if grouped.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    per_host_vals = grouped.groupby("host")["sum"].mean()
    value = float(per_host_vals.mean())
    if cache is not None:
      cache[cache_key] = value
    return value

  def job_value_mean(self,
                     jt,
                     typename=None,
                     events=None,
                     conv=1.0,
                     cache=None,
                     rows_cache=None):
    """Mean sampled ``value`` by host and 5m bucket (same bucketing as ``job_arc``)."""
    import pandas as pd

    if not getattr(jt, "_base_filter", None):
      return None
    base = jt._base_filter
    hosts = base.get("host__in") or []
    if not hosts:
      return None
    cache_key = None
    if cache is not None:
      cache_key = (
          "vm",
          _coerce_metrics_identity_str(typename),
          _hashable_metric_events_signature(events),
          float(conv),
      )
      if cache_key in cache:
        return cache[cache_key]
    tkw = _jid_table_host_data_time_kwargs(base)
    if not tkw:
      return None
    rows = []
    for typ in type_probe_names(typename):
      rows = _host_data_metric_rows_batched(
          tkw, hosts, typ, events, "value", rows_cache=rows_cache)
      if rows:
        break
    if not rows:
      if cache is not None:
        cache[cache_key] = None
      return None
    df = pd.DataFrame(rows)
    if df.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
      df["time"] = pd.to_datetime(df["time"])
    df["bucket"] = df["time"].dt.floor("5min")
    grouped = (
        df.groupby(["host", "bucket"], as_index=False)["value"].mean().rename(
            columns={"bucket": "time", "value": "sum"}
        )
    )
    grouped["sum"] = grouped["sum"] * float(conv)
    if grouped.empty or "host" not in grouped.columns:
      if cache is not None:
        cache[cache_key] = None
      return None
    first_idx = grouped.groupby("host", group_keys=False).head(1).index
    grouped = grouped.drop(index=first_idx)
    if grouped.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    per_host_vals = grouped.groupby("host")["sum"].mean()
    value = float(per_host_vals.mean())
    if cache is not None:
      cache[cache_key] = value
    return value

  def _job_arc_avg_flops(self, jt, cache=None, rows_cache=None):
    """GFLOP/s from AMD PMC, else Intel FP_ARITH/SSE, else ARM host_cpu_hw estimate."""
    for pmc_typ in amd_pmc_type_names():
      for flop_ev in fp_ops_retired_event_names():
        v = self.job_arc(
            jt,
            typename=pmc_typ,
            events=[flop_ev],
            conv=1e-9,
            units="GF",
            cache=cache,
            rows_cache=rows_cache,
        )
        if v is not None:
          return v, pmc_typ
    for core_typ in core_pmc_types_probe_order():
      v = self.job_arc(
          jt,
          typename=core_typ,
          events=list(INTEL_FP_ARITH_ALL_EVENTS),
          conv=1e-9,
          units="GF",
          cache=cache,
          rows_cache=rows_cache,
      )
      if v is not None:
        return v, core_typ
    for core_typ in core_pmc_types_probe_order():
      total = None
      for ev, weight in INTEL_LEGACY_SSE_FLOP_EVENTS:
        part = self.job_arc(
            jt,
            typename=core_typ,
            events=[ev],
            conv=1e-9 * weight,
            units="GF",
            cache=cache,
            rows_cache=rows_cache,
        )
        if part is not None:
          total = part if total is None else total + part
      if total is not None and total > 0:
        return total, core_typ
    for hw_typ in host_cpu_hw_type_names():
      for flop_ev in arm_est_flops_event_names():
        v = self.job_arc(
            jt,
            typename=hw_typ,
            events=[flop_ev],
            conv=1e-9,
            units="GF",
            cache=cache,
            rows_cache=rows_cache,
        )
        if v is not None:
          return v, hw_typ
    return None, None

  def _job_arc_avg_mbw(self, jt, cache=None, rows_cache=None):
    """Memory bandwidth (GB/s): AMD DF MBW channels, else Intel/ARM IMC CAS sum."""
    amd_bw_events = [
        "MBW_CHANNEL_0",
        "MBW_CHANNEL_1",
        "MBW_CHANNEL_2",
        "MBW_CHANNEL_3",
        "MBW_CHANNEL_4",
        "MBW_CHANNEL_5",
        "MBW_CHANNEL_6",
        "MBW_CHANNEL_7",
    ]
    for df_typ in amd_df_type_names():
      v = self.job_arc(
          jt,
          typename=df_typ,
          events=amd_bw_events,
          conv=2 / (1024 ** 3),
          units="GB/s",
          cache=cache,
          rows_cache=rows_cache,
      )
      if v is not None:
        return v, df_typ
    cas_conv = 64 / (1024 ** 3)
    for imc_typ in imc_types_probe_order():
      for read_ev, write_ev in dram_cas_read_write_pairs():
        v = self.job_arc(
            jt,
            typename=imc_typ,
            events=[read_ev, write_ev],
            conv=cas_conv,
            units="GB/s",
            cache=cache,
            rows_cache=rows_cache,
        )
        if v is not None:
          return v, imc_typ
    for imc_typ in arm_imc_types_probe_order():
      for read_ev, write_ev in dram_cas_read_write_pairs():
        v = self.job_arc(
            jt,
            typename=imc_typ,
            events=[read_ev, write_ev],
            conv=cas_conv,
            units="GB/s",
            cache=cache,
            rows_cache=rows_cache,
        )
        if v is not None:
          return v, imc_typ
    for hw_typ in host_cpu_hw_type_names():
      v = self.job_arc(
          jt,
          typename=hw_typ,
          events=["ARM_DRAM_BW_BYTES"],
          conv=1 / (1024 ** 3),
          units="GB/s",
          cache=cache,
          rows_cache=rows_cache,
      )
      if v is not None:
        return v, hw_typ
    return None, None

  def _job_arc_avg_sharedfs_iops(self, jt, cache=None, rows_cache=None):
    """Shared filesystem IOPS from Lustre llite and NFS operation counters.

    Returns summed contribution from available sources and a representative type.
    """
    total = 0.0
    used = []
    llite = self.job_arc(
        jt,
        typename="llite",
        events=[
            "open", "close", "mmap", "fsync", "setattr", "truncate", "flock",
            "getattr", "statfs", "alloc_inode", "setxattr", "listxattr",
            "removexattr", "readdir", "create", "lookup", "link", "unlink",
            "symlink", "mkdir", "rmdir", "mknod", "rename",
        ],
        conv=1,
        units="iops",
        cache=cache,
        rows_cache=rows_cache,
    )
    if llite is not None:
      total += llite
      used.append("llite")
    nfs = self.job_arc(
        jt,
        typename="nfs",
        events=["READ_ops", "WRITE_ops"],
        conv=1,
        units="iops",
        cache=cache,
        rows_cache=rows_cache,
    )
    if nfs is not None:
      total += nfs
      used.append("nfs")
    if not used:
      return None, None
    return total, used[0]

  def _job_arc_avg_sharedfs_bw(self, jt, cache=None, rows_cache=None):
    """Shared filesystem bandwidth from Lustre llite and NFS byte counters.

    Returns summed contribution from available sources and a representative type.
    """
    conv = 1.0 / (1024 * 1024)
    total = 0.0
    used = []
    llite = self.job_arc(
        jt,
        typename="llite",
        events=["read_bytes", "write_bytes"],
        conv=conv,
        units="MB/s",
        cache=cache,
        rows_cache=rows_cache,
    )
    if llite is not None:
      total += llite
      used.append("llite")
    nfs = self.job_arc(
        jt,
        typename="nfs",
        events=[
            "normal_read", "normal_write",
            "direct_read", "direct_write",
            "server_read", "server_write",
        ],
        conv=conv,
        units="MB/s",
        cache=cache,
        rows_cache=rows_cache,
    )
    if nfs is not None:
      total += nfs
      used.append("nfs")
    if not used:
      return None, None
    return total, used[0]

  def _job_arc_avg_ibbw(self, jt, cache=None, rows_cache=None):
    """Fabric bandwidth from IB/OPA, with Ethernet fallback when unavailable."""
    v = self.job_arc(
        jt,
        typename=HOST_IB_TYPE,
        events=["port_xmit_data", "port_rcv_data"],
        conv=1.0 / (1024 * 1024),
        units="MB/s",
        cache=cache,
        rows_cache=rows_cache,
        nonnegative_rate=True,
    )
    if v is not None:
      return v, HOST_IB_TYPE
    v = self.job_arc(
        jt,
        typename="opa",
        events=["PortXmitData", "PortRcvData"],
        conv=1.0 / 125000,
        units="MB/s",
        cache=cache,
        rows_cache=rows_cache,
        nonnegative_rate=True,
    )
    if v is not None:
      return v, "opa"
    v = self.job_arc(
        jt,
        typename="net",
        events=["rx_bytes", "tx_bytes"],
        conv=1.0 / (1024 * 1024),
        units="MB/s",
        cache=cache,
        rows_cache=rows_cache,
        nonnegative_rate=True,
    )
    if v is not None:
      return v, "net"
    return None, None

  # Compute metric
  def compute_metrics(self, job):
    """Compute metrics for one job; return dict with rows (metrics_data-shaped dicts) and distinct_time_count.

        distinct_time_count is the sum over hosts of COUNT(DISTINCT time) in
        jid_table._host_data_qs() for this job (not the global distinct time count).
        """
    results = []

    telemetry_first_time = getattr(job, "telemetry_first_time", None)
    telemetry_last_time = getattr(job, "telemetry_last_time", None)
    if telemetry_first_time is None and telemetry_last_time is None:
      telemetry_first_time, telemetry_last_time = (
          _in_window_telemetry_bounds_for_job(job)
      )

    # Job-scoped host_data via ORM (no temp table)
    with jid_table.jid_table(job.jid) as jt:
      simple_metric_cache = {}
      host_data_rows_cache = {}

      job_view = _JobForMetrics(jt)
      distinct_time_count = job_view.per_host_distinct_time_sum

      if job_view.times.size == 0:
        # Still persist schema + job-detail GPU/FSIO aggregates (ORM paths) for API.
        try:
          sch = getattr(jt, "schema", None) or {}
          job.host_data_schema_json = dict(sch) if isinstance(sch, dict) else {}
          job.save(update_fields=["host_data_schema_json"])
        except Exception:
          pass
        from hpcperfstats.analysis.metrics.gpu_job_detail_summary import (
            compute_job_gpu_summary_tuple,
        )

        gpu_active, gpu_max, gpu_mean, gpu_count = compute_job_gpu_summary_tuple(jt)
        detail_values = (gpu_active, gpu_max, gpu_mean, gpu_count)
        for i, (metric_name, row_type, units) in enumerate(_GPU_JOB_DETAIL_CATALOG):
          val = detail_values[i]
          if val is None:
            results.append({
                "jid": job,
                "type": row_type,
                "metric": metric_name,
                "units": units,
                "value": None,
                "no_data_reason": NO_GPU_AGGREGATE_TELEMETRY,
            })
          else:
            store_val = (
                float(int(val)) if metric_name in (
                    "detail_gpu_active", "detail_gpu_count") else float(val))
            results.append({
                "jid": job,
                "type": row_type,
                "metric": metric_name,
                "units": units,
                "value": store_val,
                "no_data_reason": None,
            })
        avg_g_val = gpu_mean
        if avg_g_val is None:
          results.append({
              "jid": job,
              "type": "gpu",
              "metric": "avg_gpuutil",
              "units": "%",
              "value": None,
              "no_data_reason": _COMPLEX_NO_DATA_REASONS["avg_gpuutil"],
          })
        else:
          results.append({
              "jid": job,
              "type": "gpu",
              "metric": "avg_gpuutil",
              "units": "%",
              "value": float(avg_g_val),
              "no_data_reason": None,
          })
        for row in compute_job_detail_fsio_metric_rows(jt):
          results.append({"jid": job, **row})
        done_metrics = (
            _coerced_metric_name_set(m for m, _, _ in _GPU_JOB_DETAIL_CATALOG)
            | _coerced_metric_name_set(["avg_gpuutil"])
            | _coerced_metric_name_set(m for m, _, _ in fsio_job_detail_catalog())
        )
        for entry in job_metrics_catalog_entries():
          catalog_metric = _coerce_metrics_identity_str(entry["metric"])
          if catalog_metric in done_metrics:
            continue
          results.append({
              "jid": job,
              "type": _coerce_metrics_identity_str(entry["type"]),
              "metric": catalog_metric,
              "units": _coerce_metrics_identity_str(entry["units"]),
              "value": None,
              "no_data_reason": NO_TIME_SERIES_MSG,
          })
        return {
            "rows": _sanitize_metrics_compute_rows(results),
            "distinct_time_count": distinct_time_count,
            "telemetry_first_time": telemetry_first_time,
            "telemetry_last_time": telemetry_last_time,
        }

      for metric_name, metric_obj in self.simple_metrics_list.items():
        if metric_name == "avg_flops":
          value, flops_typename = self._job_arc_avg_flops(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          row_type = flops_typename or metric_obj["typename"]
        elif metric_name == "avg_mbw":
          value, mbw_typename = self._job_arc_avg_mbw(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          row_type = mbw_typename or metric_obj["typename"]
        elif metric_name == "avg_sharedfs_iops":
          value, fs_typename = self._job_arc_avg_sharedfs_iops(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          row_type = fs_typename or metric_obj["typename"]
        elif metric_name == "avg_sharedfs_bw":
          value, fs_typename = self._job_arc_avg_sharedfs_bw(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          row_type = fs_typename or metric_obj["typename"]
        elif metric_name == "avg_ibbw":
          value, fabric_typename = self._job_arc_avg_ibbw(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          row_type = fabric_typename or metric_obj["typename"]
        elif metric_name == "avg_fabric_mb_per_gflops":
          gf, flops_typename = self._job_arc_avg_flops(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          fb, fabric_typename = self._job_arc_avg_ibbw(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          if (
              gf is not None and fb is not None
              and float(gf) > 0 and float(fb) >= 0
          ):
            value = float(fb) / float(gf)
            row_type = fabric_typename or flops_typename or metric_obj[
                "typename"]
          else:
            value = None
            row_type = (
                fabric_typename or flops_typename or metric_obj["typename"]
            )
        elif metric_name in (
            "avg_tensor_active",
            "avg_fp16_active",
            "avg_fp32_active",
            "avg_fp64_active",
        ):
          metric_event = {
              "avg_tensor_active": "tensor_active",
              "avg_fp16_active": "fp16_active",
              "avg_fp32_active": "fp32_active",
              "avg_fp64_active": "fp64_active",
          }[metric_name]
          value = None
          row_type = "nvidia_gpu"
          for gt in ("nvidia_gpu", "amd_gpu"):
            v = self.job_value_mean(
                jt,
                typename=gt,
                events=[metric_event],
                conv=1.0,
                cache=simple_metric_cache,
                rows_cache=host_data_rows_cache,
            )
            if v is not None and float(v) > 0:
              value = float(v)
              row_type = gt
              break
        elif metric_name == "avg_gpu_mem_bw_gbps":
          value = None
          row_type = "nvidia_gpu"
          for gt in ("nvidia_gpu", "amd_gpu"):
            v = self.job_value_mean(
                jt,
                typename=gt,
                events=["gpu_mem_bw_bytes_rate"],
                conv=1.0 / 1e9,
                cache=simple_metric_cache,
                rows_cache=host_data_rows_cache,
            )
            if v is not None and float(v) > 0:
              value = float(v)
              row_type = gt
              break
        elif metric_name == "avg_fabric_mb_per_avg_tensor":
          fb, fabric_typename = self._job_arc_avg_ibbw(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          ts = self.job_value_mean(
              jt,
              typename="nvidia_gpu",
              events=["tensor_active"],
              conv=1.0,
              cache=simple_metric_cache,
              rows_cache=host_data_rows_cache,
          )
          if ts is None:
            ts = self.job_value_mean(
                jt,
                typename="amd_gpu",
                events=["tensor_active"],
                conv=1.0,
                cache=simple_metric_cache,
                rows_cache=host_data_rows_cache,
            )
          if (
              fb is not None and ts is not None
              and float(ts) > 1e-6 and float(fb) >= 0
          ):
            value = float(fb) / (float(ts) / 100.0)
            row_type = fabric_typename or metric_obj["typename"]
          else:
            value = None
            row_type = fabric_typename or metric_obj["typename"]
        else:
          value = self.job_arc(
              jt,
              cache=simple_metric_cache,
              rows_cache=host_data_rows_cache,
              **metric_obj)
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

      from hpcperfstats.analysis.metrics.gpu_job_detail_summary import (
          compute_job_gpu_summary_tuple as _compute_job_gpu_summary_tuple,
      )

      gpu_active, gpu_max, gpu_mean, gpu_count = _compute_job_gpu_summary_tuple(jt)
      detail_values = (gpu_active, gpu_max, gpu_mean, gpu_count)
      for i, (metric_name, row_type, units) in enumerate(_GPU_JOB_DETAIL_CATALOG):
        val = detail_values[i]
        if val is None:
          results.append({
              "jid": job,
              "type": row_type,
              "metric": metric_name,
              "units": units,
              "value": None,
              "no_data_reason": NO_GPU_AGGREGATE_TELEMETRY,
          })
        else:
          if metric_name in ("detail_gpu_active", "detail_gpu_count"):
            store_val = float(int(val))
          else:
            store_val = float(val)
          results.append({
              "jid": job,
              "type": row_type,
              "metric": metric_name,
              "units": units,
              "value": store_val,
              "no_data_reason": None,
          })
      avg_g_val = gpu_mean
      if avg_g_val is None:
        results.append({
            "jid": job,
            "type": "gpu",
            "metric": "avg_gpuutil",
            "units": "%",
            "value": None,
            "no_data_reason": _COMPLEX_NO_DATA_REASONS["avg_gpuutil"],
        })
      else:
        results.append({
            "jid": job,
            "type": "gpu",
            "metric": "avg_gpuutil",
            "units": "%",
            "value": float(avg_g_val),
            "no_data_reason": None,
        })

      for row in compute_job_detail_fsio_metric_rows(jt):
        results.append({"jid": job, **row})

      try:
        sch = getattr(jt, "schema", None) or {}
        job.host_data_schema_json = dict(sch) if isinstance(sch, dict) else {}
        job.save(update_fields=["host_data_schema_json"])
      except Exception:
        pass

      u = utils(job_view)

      for metric_name in self.complex_metrics_list:
        if metric_name == "max_node_power_est_w":
          from hpcperfstats.analysis.gen.node_power_est import (
              max_node_power_est_w as _max_npe,
          )
          value = _max_npe(jt)
          typename, units = "job", "W"
        elif metric_name == "avg_node_power_est_w":
          from hpcperfstats.analysis.gen.node_power_est import (
              mean_node_power_est_w as _mean_npe,
          )
          value = _mean_npe(jt)
          typename, units = "job", "W"
        else:
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

    return {
        "rows": _sanitize_metrics_compute_rows(results),
        "distinct_time_count": distinct_time_count,
        "telemetry_first_time": telemetry_first_time,
        "telemetry_last_time": telemetry_last_time,
    }


def job_metrics_catalog_entries():
  """Ordered catalog of every job-level metric for UI and completeness checks.

  Short labels for the Job detail table are defined in
  ``hpcperfstats.analysis.metrics.job_metric_display_labels.JOB_METRIC_SHORT_LABELS``
  (Python) and mirrored in the SPA
  ``hpcperfstats/site/frontend/src/utils/jobMetricDisplayLabels.js``.
  """
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
        "type": _coerce_metrics_identity_str(spec["typename"]),
        "metric": _coerce_metrics_identity_str(metric),
        "units": _coerce_metrics_identity_str(spec["units"]),
    })
  for name in m.complex_metrics_list:
    t, u = _COMPLEX_PLACEHOLDER_TYPE_UNITS[name]
    out.append({
        "type": _coerce_metrics_identity_str(t),
        "metric": _coerce_metrics_identity_str(name),
        "units": _coerce_metrics_identity_str(u),
    })
  for metric, t, u in _GPU_JOB_DETAIL_CATALOG:
    out.append({
        "type": _coerce_metrics_identity_str(t),
        "metric": _coerce_metrics_identity_str(metric),
        "units": _coerce_metrics_identity_str(u),
    })
  agt, agu = _COMPLEX_PLACEHOLDER_TYPE_UNITS["avg_gpuutil"]
  out.append({
      "type": _coerce_metrics_identity_str(agt),
      "metric": _coerce_metrics_identity_str("avg_gpuutil"),
      "units": _coerce_metrics_identity_str(agu),
  })
  for metric, t, u in fsio_job_detail_catalog():
    out.append({
        "type": _coerce_metrics_identity_str(t),
        "metric": _coerce_metrics_identity_str(metric),
        "units": _coerce_metrics_identity_str(u),
    })
  return out


def expected_job_metric_row_count():
  return len(job_metrics_catalog_entries())


def build_job_metrics_display_list(job):
  """API: full metrics_list with a row per catalog metric (value or no_data_reason)."""
  by_metric = {
      _coerce_metrics_identity_str(o.metric): o for o in job.metrics_data_set.all()
  }
  out = []
  for spec in job_metrics_catalog_entries():
    row = by_metric.get(_coerce_metrics_identity_str(spec["metric"]))
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
          "type": _coerce_metrics_identity_str(row.type),
          "metric": _coerce_metrics_identity_str(row.metric),
          "units": _coerce_metrics_identity_str(row.units),
          "value": row.value,
          "no_data_reason": row.no_data_reason,
      })
  # Job detail UI: show catalog metrics with values or other reasons first; missing rows last.
  out.sort(key=lambda r: r.get("no_data_reason") == METRIC_NOT_COMPUTED_YET)
  return out


def _gate_failure_catalog_already_clean(jid):
  """True when jid already has a full insufficient gate-failure catalog and no artifacts."""
  expected = expected_job_metric_row_count()
  qs = metrics_data.objects.filter(jid_id=jid)
  if qs.count() != expected:
    return False
  if qs.filter(value__isnull=False).exists():
    return False
  if qs.exclude(no_data_reason=INSUFFICIENT_DATA_FOR_METRICS_PROCESSING).exists():
    return False
  from hpcperfstats.site.machine.models import job_detail_artifact, job_plot_artifact

  if job_plot_artifact.objects.filter(jid_id=jid).exists():
    return False
  if job_detail_artifact.objects.filter(jid_id=jid).exists():
    return False
  return True


def persist_window_coverage_gate_failure(
    jid,
    *,
    telemetry_first_time=None,
    telemetry_last_time=None,
    distinct_time_count=None,
):
  """Remove stale metrics/plots and persist full catalog with gate-failure reason.

  Returns True when a write occurred; False when idempotent skip or invalid jid.
  """
  jid = str(jid or "").strip()
  if not jid:
    return False
  if _gate_failure_catalog_already_clean(jid):
    return False

  from hpcperfstats.site.machine.cache_utils import (
      invalidate_jid_derived_cache_keys,
      invalidate_job_plot_cache_keys_for_jids,
  )
  from hpcperfstats.site.machine.job_plot_artifacts import (
      get_live_distinct_time_count_for_jid,
  )

  invalidate_job_plot_cache_keys_for_jids([jid])
  invalidate_jid_derived_cache_keys([jid])

  if distinct_time_count is None:
    distinct_time_count = get_live_distinct_time_count_for_jid(jid)

  metrics_data.objects.filter(jid_id=jid).delete()
  job_obj = job_data.objects.filter(pk=jid).first()
  job_ref = job_obj if job_obj is not None else jid
  rows = [
      {
          "jid": job_ref,
          "type": entry["type"],
          "metric": entry["metric"],
          "units": entry["units"],
          "value": None,
          "no_data_reason": INSUFFICIENT_DATA_FOR_METRICS_PROCESSING,
      }
      for entry in job_metrics_catalog_entries()
  ]
  _persist_metrics_batch(
      rows,
      distinct_time_count,
      telemetry_first_time=telemetry_first_time,
      telemetry_last_time=telemetry_last_time,
  )
  return True


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
    events = frozenset(_coerce_metrics_identity_str(e) for e in schema.events)
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
    if schema is None or not _schema_has_events(
        schema, "rx_bytes", "tx_bytes"):
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
      # Cumulative byte counters should not decrease; negative means reset or bad data.
      if b < 0 or not np.isfinite(b):
        continue
      per_host.append(b / denom)
    if not per_host:
      return None, typename, 'MB/s'
    value = float(mean(per_host))
    if value == 0:
      return None, typename, 'MB/s'
    return value, typename, 'MB/s'


class avg_gpuutil():
  """Average GPU utilization (%) from nvidia_gpu or amd_gpu.

    """

  def _avg_gpuutil_for_event(self, u, typename, event_name):
    """Mean utilization (%) for one ``typename`` / ``event_name``, or None if unusable."""
    schema, _stats = u.get_type(typename)
    if schema is None or event_name not in schema.events:
      return None
    ui = schema[event_name].index
    per_host = []
    for hostname, stats in _stats.items():
      window = stats[1:-1, ui]
      if window.size == 0:
        continue
      per_host.append(float(mean(window)))
    if not per_host:
      return None
    value = float(mean(per_host))
    if value == 0:
      return None
    return value, typename, '%'

  def compute_metric(self, u):
    # nvidia: same order as summary plot / job_detail — try gpu_util, then utilization.
    for event_name in ("gpu_util", "utilization"):
      r = self._avg_gpuutil_for_event(u, "nvidia_gpu", event_name)
      if r is not None:
        return r
    r = self._avg_gpuutil_for_event(u, "amd_gpu", "gpu_util")
    if r is not None:
      return r
    return None, "gpu", '%'


class avg_packetsize():
  """Average packet size (MB) from host_ib or opa port xmit/rcv data and packets.

    """

  def compute_metric(self, u):
    ib_schema, ib_stats = u.get_type(HOST_IB_TYPE)
    if ib_schema is not None and _schema_has_events(
        ib_schema,
        "port_xmit_pkts",
        "port_rcv_pkts",
        "port_xmit_data",
        "port_rcv_data",
    ):
      typename = HOST_IB_TYPE
      schema, _stats = ib_schema, ib_stats
      tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
      tb, rb = schema["port_xmit_data"].index, schema["port_rcv_data"].index
      conv2mb = 1024 * 1024
    else:
      opa_schema, opa_stats = u.get_type("opa")
      if opa_schema is not None and _schema_has_events(
          opa_schema,
          "PortXmitPkts",
          "PortRcvPkts",
          "PortXmitData",
          "PortRcvData",
      ):
        typename = "opa"
        schema, _stats = opa_schema, opa_stats
        tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index
        tb, rb = schema["PortXmitData"].index, schema["PortRcvData"].index
        conv2mb = 125000
      else:
        net_schema, net_stats = u.get_type("net")
        if net_schema is None or not _schema_has_events(
            net_schema,
            "tx_packets",
            "rx_packets",
            "tx_bytes",
            "rx_bytes",
        ):
          return None, HOST_IB_TYPE, 'MB'
        typename = "net"
        schema, _stats = net_schema, net_stats
        tx, rx = schema["tx_packets"].index, schema["rx_packets"].index
        tb, rb = schema["tx_bytes"].index, schema["rx_bytes"].index
        conv2mb = 1024 * 1024

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
  """Maximum fabric bandwidth (MB/s) from host_ib or opa port data.

    """

  def compute_metric(self, u):
    max_bw = 0
    ib_schema, ib_stats = u.get_type(HOST_IB_TYPE)
    if ib_schema is not None and _schema_has_events(
        ib_schema, "port_xmit_data", "port_rcv_data"):
      typename = HOST_IB_TYPE
      schema, _stats = ib_schema, ib_stats
      tx, rx = schema["port_xmit_data"].index, schema["port_rcv_data"].index
      conv2mb = 1024 * 1024
    else:
      opa_schema, opa_stats = u.get_type("opa")
      if opa_schema is not None and _schema_has_events(
          opa_schema, "PortXmitData", "PortRcvData"):
        typename = "opa"
        schema, _stats = opa_schema, opa_stats
        tx, rx = schema["PortXmitData"].index, schema["PortRcvData"].index
        conv2mb = 125000
      else:
        net_schema, net_stats = u.get_type("net")
        if net_schema is None or not _schema_has_events(
            net_schema, "tx_bytes", "rx_bytes"):
          return None, HOST_IB_TYPE, 'MB/s'
        typename = "net"
        schema, _stats = net_schema, net_stats
        tx, rx = schema["tx_bytes"].index, schema["rx_bytes"].index
        conv2mb = 1024 * 1024
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [tx, rx], conv2mb)
    if cluster_peak is not None:
      return cluster_peak, typename, 'MB/s'
    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(_add_arrays(stats[:, tx], stats[:, rx]), u.t)
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
    if schema is None or not _schema_has_events(
        schema, "tx_bytes", "rx_bytes"):
      return None, typename, 'MB/s'
    max_bw = 0.0
    tx, rx = schema["tx_bytes"].index, schema["rx_bytes"].index
    div = 1024 * 1024
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [tx, rx], div)
    if cluster_peak is not None:
      return cluster_peak, typename, 'MB/s'
    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(_add_arrays(stats[:, tx], stats[:, rx]), u.t)
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
    mds_cols = [
        "open", "close", "mmap", "fsync", "setattr", "truncate", "flock",
        "getattr", "statfs", "alloc_inode", "setxattr", "listxattr",
        "removexattr", "readdir", "create", "lookup", "link", "unlink",
        "symlink", "mkdir", "rmdir", "mknod", "rename",
    ]
    if schema is not None and _schema_has_events(schema, *mds_cols):
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
    nfs_typename = "nfs"
    nfs_schema, nfs_stats = u.get_type(nfs_typename)
    if nfs_schema is not None and all(
        ev in nfs_schema.events for ev in ("READ_ops", "WRITE_ops")
    ):
      tx, rx = nfs_schema["READ_ops"].index, nfs_schema["WRITE_ops"].index
      cluster_peak = _peak_interval_rate_from_cluster_mean(
          u, nfs_typename, [tx, rx], 1)
      if cluster_peak is not None:
        max_mds = max(max_mds, cluster_peak)
      for hostname, stats in nfs_stats.items():
        ratio = _per_interval_rate(_add_arrays(stats[:, tx], stats[:, rx]), u.t)
        fin = ratio[np.isfinite(ratio)]
        if fin.size > 0:
          max_mds = max(max_mds, fin.max())
    if max_mds == 0:
      return None, "llite", 'iops'
    value = max_mds
    return value, "llite", 'iops'


class max_packetrate():
  """Maximum packet rate (#/s) from host_ib or opa port xmit/rcv packets.

    """

  def compute_metric(self, u):
    max_pr = 0
    ib_schema, ib_stats = u.get_type(HOST_IB_TYPE)
    if ib_schema is not None and _schema_has_events(
        ib_schema, "port_xmit_pkts", "port_rcv_pkts"):
      typename = HOST_IB_TYPE
      schema, _stats = ib_schema, ib_stats
      tx, rx = schema["port_xmit_pkts"].index, schema["port_rcv_pkts"].index
    else:
      opa_schema, opa_stats = u.get_type("opa")
      if opa_schema is not None and _schema_has_events(
          opa_schema, "PortXmitPkts", "PortRcvPkts"):
        typename = "opa"
        schema, _stats = opa_schema, opa_stats
        tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index
      else:
        net_schema, net_stats = u.get_type("net")
        if net_schema is None or not _schema_has_events(
            net_schema, "tx_packets", "rx_packets"):
          return None, HOST_IB_TYPE, '#/s'
        typename = "net"
        schema, _stats = net_schema, net_stats
        tx, rx = schema["tx_packets"].index, schema["rx_packets"].index

    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [tx, rx], 1)
    if cluster_peak is not None:
      return cluster_peak, typename, '#/s'

    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(_add_arrays(stats[:, tx], stats[:, rx]), u.t)
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
    if schema is None or not _schema_has_events(
        schema, "MemUsed", "Slab", "FilePages"):
      return None, typename, 'GiB'
    for hostname, stats in _stats.items():
      mem_arr = (stats[:, schema["MemUsed"].index] -
                 stats[:, schema["Slab"].index] -
                 stats[:, schema["FilePages"].index])
      peak = _finite_amax(mem_arr)
      if peak is not None:
        max_memusage = max(max_memusage, peak)
    if max_memusage == 0:
      return None, typename, 'GiB'
    value = max_memusage / (2.**30)
    return value, typename, 'GiB'


def _flops_weighted_events_for_schema(schema):
  """Return [(event, weight), ...] for total FLOP-equivalent arc columns, or None."""
  if schema is None:
    return None
  if "FLOPS" in schema:
    return [("FLOPS", 1.0)]
  fp = [(e, 1.0) for e in INTEL_FP_ARITH_ALL_EVENTS if e in schema]
  if fp:
    return fp
  leg = [(e, float(w)) for e, w in INTEL_LEGACY_SSE_FLOP_EVENTS if e in schema]
  if leg:
    return leg
  if "ARM_EST_FLOPS" in schema:
    return [("ARM_EST_FLOPS", 1.0)]
  return None


def _node_imbalance_percent_weighted(u, typename, weighted_events):
  """Like ``node_imbalance`` but on a weighted sum of counter columns."""
  schema, _stats = u.get_type(typename)
  if schema is None or not _stats:
    return None
  idx_w = []
  for ev, w in weighted_events:
    if ev not in schema:
      return None
    idx_w.append((schema[ev].index, float(w)))
  max_usage = zeros(u.nt - 1)
  for hostname, stats in _stats.items():
    s = np.zeros(stats.shape[0], dtype=np.float64)
    for j, w in idx_w:
      s = s + w * stats[:, j].astype(np.float64)
    rate = _per_interval_rate(s, u.t)
    max_usage = maximum(max_usage, np.nan_to_num(rate, nan=-np.inf))
  max_imbalance = []
  for hostname, stats in _stats.items():
    s = np.zeros(stats.shape[0], dtype=np.float64)
    for j, w in idx_w:
      s = s + w * stats[:, j].astype(np.float64)
    rate = _per_interval_rate(s, u.t)
    valid = (max_usage > 0) & np.isfinite(rate)
    if np.any(valid):
      rel = (max_usage[valid] - rate[valid]) / max_usage[valid]
      max_imbalance += [mean(rel)]
    else:
      max_imbalance += [float("nan")]
  if not max_imbalance:
    return None
  value = 100 * amax([0. if isnan(x) else x for x in max_imbalance])
  return value


class max_opa_congestion_rate():
  """Peak interval rate of summed OPA congestion-related counters (events/s)."""

  def compute_metric(self, u):
    typename = "opa"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, "#/s"
    cands = (
        "PortXmitWait",
        "SwPortCongestion",
        "PortRcvFECN",
        "PortRcvBECN",
    )
    indices = [schema[ev].index for ev in cands if ev in schema]
    if not indices:
      return None, typename, "#/s"
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, indices, 1.0)
    if cluster_peak is not None:
      return cluster_peak, typename, "#/s"
    max_r = 0.0
    for hostname, stats in _stats.items():
      s = np.zeros(stats.shape[0], dtype=np.float64)
      for j in indices:
        s = s + stats[:, j].astype(np.float64)
      ratio = _per_interval_rate(s, u.t)
      fin = ratio[np.isfinite(ratio)]
      if fin.size > 0:
        max_r = max(max_r, float(fin.max()))
    if max_r <= 0:
      return None, typename, "#/s"
    return max_r, typename, "#/s"


class max_numa_remote_rate():
  """Peak interval rate of NUMA remote-access counters (miss/foreign/other_node)."""

  def compute_metric(self, u):
    typename = "numa"
    schema, _stats = u.get_type(typename)
    if schema is None:
      return None, typename, "#/s"
    cands = ("numa_miss", "numa_foreign", "other_node")
    indices = [schema[ev].index for ev in cands if ev in schema]
    if not indices:
      return None, typename, "#/s"
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, indices, 1.0)
    if cluster_peak is not None:
      return cluster_peak, typename, "#/s"
    max_r = 0.0
    for hostname, stats in _stats.items():
      s = np.zeros(stats.shape[0], dtype=np.float64)
      for j in indices:
        s = s + stats[:, j].astype(np.float64)
      ratio = _per_interval_rate(s, u.t)
      fin = ratio[np.isfinite(ratio)]
      if fin.size > 0:
        max_r = max(max_r, float(fin.max()))
    if max_r <= 0:
      return None, typename, "#/s"
    return max_r, typename, "#/s"


class flops_node_imbalance():
  """FLOPs rate imbalance across nodes (%), same construction as ``node_imbalance``."""

  def compute_metric(self, u):
    typename = u.pmc
    if not typename:
      return None, "pmc", "%"
    schema, _stats = u.get_type(typename)
    we = _flops_weighted_events_for_schema(schema)
    if not we or not _stats:
      return None, typename, "%"
    v = _node_imbalance_percent_weighted(u, typename, we)
    if v is None:
      return None, typename, "%"
    return v, typename, "%"


def _dram_bw_weighted_events_for_imbalance(u):
  """Return (typename, [(event, weight), ...]) for DRAM CAS/MBW imbalance, or (None, None)."""
  for df_typ in amd_df_type_names():
    schema_df, _, _ = resolve_get_type(u, (df_typ,))
    if schema_df is not None:
      chans = [f"MBW_CHANNEL_{i}" for i in range(8)]
      found = [c for c in chans if c in schema_df]
      if found:
        return df_typ, [(c, 1.0) for c in found]
  imc = u.imc
  if not imc:
    return None, None
  schema_imc, _, imc_typ = resolve_get_type(u, (imc,))
  if schema_imc is None:
    return None, None
  for read_ev, write_ev in dram_cas_read_write_pairs():
    pair = []
    if read_ev in schema_imc:
      pair.append((read_ev, 1.0))
    if write_ev in schema_imc:
      pair.append((write_ev, 1.0))
    if pair:
      return imc_typ, pair
  return None, None


def _node_imbalance_instantaneous_percent(u, typename, event_name):
  """Imbalance for snapshot ``value`` columns (e.g. GPU util): per-time max vs each host."""
  schema, _stats = u.get_type(typename)
  if schema is None or event_name not in schema or not _stats:
    return None
  j = schema[event_name].index
  nt = u.nt
  max_per_t = np.full(nt, -np.inf, dtype=np.float64)
  for hostname, stats in _stats.items():
    max_per_t = np.maximum(max_per_t, stats[:, j].astype(np.float64))
  max_imbalance = []
  for hostname, stats in _stats.items():
    v = stats[:, j].astype(np.float64)
    valid = max_per_t > 0
    if not np.any(valid):
      max_imbalance.append(float("nan"))
      continue
    rel = (max_per_t[valid] - v[valid]) / max_per_t[valid]
    max_imbalance.append(float(mean(rel)))
  if not max_imbalance:
    return None
  return 100 * amax([0. if isnan(x) else x for x in max_imbalance])


class max_gpu_power():
  """Peak GPU power draw (W) from ``nvidia_gpu`` or ``amd_gpu`` samples."""

  def compute_metric(self, u):
    mx = 0.0
    used = None
    for typename in ("nvidia_gpu", "amd_gpu"):
      schema, _stats = u.get_type(typename)
      if schema is None or "power_usage" not in schema or not _stats:
        continue
      j = schema["power_usage"].index
      for hostname, stats in _stats.items():
        col = stats[:, j].astype(float)
        peak = _finite_amax(col)
        if peak is not None:
          mx = max(mx, peak)
          used = typename
    if mx <= 0 or used is None:
      return None, "nvidia_gpu", "W"
    return mx, used, "W"


class max_gpu_link_gbps():
  """Peak PCIe+NVLink byte rate (GB/s) from ``nvidia_gpu`` ``gpu_io_link_total_bytes`` arc."""

  def compute_metric(self, u):
    typename = "nvidia_gpu"
    schema, _stats = u.get_type(typename)
    if schema is None or "gpu_io_link_total_bytes" not in schema:
      return None, typename, "GB/s"
    j = schema["gpu_io_link_total_bytes"].index
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [j], 1e9)
    if cluster_peak is not None:
      return cluster_peak, typename, "GB/s"
    max_bw = 0.0
    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(stats[:, j], u.t)
      fin = ratio[np.isfinite(ratio)]
      if fin.size > 0:
        max_bw = max(max_bw, float(fin.max()))
    if max_bw <= 0:
      return None, typename, "GB/s"
    return max_bw / 1e9, typename, "GB/s"


class max_gpu_clock_event_reasons():
  """Maximum observed DCGM clock throttle reason bitmask (opaque; non-zero implies throttling)."""

  def compute_metric(self, u):
    mx = 0
    used = None
    for typename in ("nvidia_gpu", "amd_gpu"):
      schema, _stats = u.get_type(typename)
      if schema is None or "clocks_event_reasons" not in schema or not _stats:
        continue
      j = schema["clocks_event_reasons"].index
      for hostname, stats in _stats.items():
        col = stats[:, j].astype(np.float64)
        peak = _finite_amax(col)
        if peak is not None:
          cmax = int(peak)
          if cmax > mx:
            mx = cmax
            used = typename
    if used is None or mx == 0:
      return None, "nvidia_gpu", "#"
    return float(mx), used, "#"


class dram_bw_node_imbalance():
  """DRAM bandwidth rate imbalance across nodes (%); AMD DF MBW or Intel IMC CAS."""

  def compute_metric(self, u):
    typename, we = _dram_bw_weighted_events_for_imbalance(u)
    if not typename or not we:
      return None, "imc", "%"
    v = _node_imbalance_percent_weighted(u, typename, we)
    if v is None:
      return None, typename, "%"
    return v, typename, "%"


class lnet_node_imbalance():
  """LNET tx+rx byte rate imbalance across nodes (%)."""

  def compute_metric(self, u):
    typename = "lnet"
    evw = [("tx_bytes", 1.0), ("rx_bytes", 1.0)]
    schema, _stats = u.get_type(typename)
    if schema is None or not _stats:
      return None, typename, "%"
    if not all(e in schema for e, _ in evw):
      return None, typename, "%"
    v = _node_imbalance_percent_weighted(u, typename, evw)
    if v is None:
      return None, typename, "%"
    return v, typename, "%"


class gpu_util_node_imbalance():
  """GPU utilization imbalance across nodes from snapshot ``gpu_util`` (or legacy names)."""

  def compute_metric(self, u):
    for typename, events in (
        ("nvidia_gpu", ("gpu_util", "utilization")),
        ("amd_gpu", ("gpu_util",)),
    ):
      for ev in events:
        v = _node_imbalance_instantaneous_percent(u, typename, ev)
        if v is not None:
          return v, typename, "%"
    return None, "nvidia_gpu", "%"


class tensor_node_imbalance():
  """Tensor-pipe activity imbalance across nodes (``tensor_active`` snapshot)."""

  def compute_metric(self, u):
    for typename in ("nvidia_gpu", "amd_gpu"):
      v = _node_imbalance_instantaneous_percent(u, typename, "tensor_active")
      if v is not None:
        return v, typename, "%"
    return None, "nvidia_gpu", "%"


class fabric_node_imbalance():
  """Fabric byte-rate imbalance across nodes (%); prefers ``host_ib`` then ``opa``."""

  def compute_metric(self, u):
    for typename, evw in (
        (HOST_IB_TYPE, [("port_xmit_data", 1.0), ("port_rcv_data", 1.0)]),
        ("opa", [("PortXmitData", 1.0), ("PortRcvData", 1.0)]),
    ):
      schema, _stats = u.get_type(typename)
      if schema is None or not _stats:
        continue
      if not all(e in schema for e, _ in evw):
        continue
      v = _node_imbalance_percent_weighted(u, typename, evw)
      if v is not None:
        return v, typename, "%"
    return None, HOST_IB_TYPE, "%"


class node_imbalance():
  """CPU node imbalance (%): max deviation of per-node CPU rate from max rate.

    """

  def compute_metric(self, u):
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    if schema is None or "user" not in schema:
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
    if schema is None or "user" not in schema:
      return None, typename, '%'
    tmid = (u.t[:-1] + u.t[1:]) / 2.0
    dt = diff(u.t)
    user_i = schema["user"].index
    vals = []
    for hostname, stats in _stats.items():
      rate = _per_interval_rate(stats[:, user_i], u.t)
      rate = np.nan_to_num(rate, nan=0.0, posinf=0.0, neginf=0.0)
      # Cumulative CPU jiffies are monotonic; negative dy/dt is reset/wrap/noise.
      rate = np.maximum(rate, 0.0)
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
        if not (a > 0) or not np.isfinite(a):
          continue
        # integral after time slice
        b = trapz(rate[r2], tmid[r2]) / after_window
        if not np.isfinite(b):
          continue
        # ratio of integral after time over before time
        ratio = b / a
        if not np.isfinite(ratio) or ratio < 0:
          continue
        if ratio > _TIME_IMBALANCE_MAX_SLICE_RATIO:
          continue
        vals += [ratio]
    if vals:
      value = 100 * min(vals)
      return value, typename, '%'
    else:
      return None, typename, '%'


class vecpercent_64b():
  """Percentage of 64b vectorized FLOPs vs total (from PMC events).

  Requires Intel-style FP_ARITH double events and/or legacy SSE/AVX double
  counter names. AMD ``amd64_pmc`` typically exposes only aggregate ``FLOPS``,
  so this metric usually has no data on AMD until width-resolved events exist.
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

  Same event requirements as ``vecpercent_64b``; not populated from aggregate
  AMD ``FLOPS`` alone.
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

  Uses Intel FP_ARITH single-precision events only; no AMD aggregate FLOPS path.
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

  Same as ``vecpercent_32b``: Intel FP_ARITH single events; not AMD FLOPS-wide.
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
