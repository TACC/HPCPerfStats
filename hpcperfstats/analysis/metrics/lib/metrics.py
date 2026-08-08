"""
Metric computation for jobs: simple metrics (job_arc/time_bucket) and complex
metrics (avg_freq, avg_ethbw, mem_hwm, etc.) via utils-compatible job view.
Results written to metrics_data.

DB access is process-safe: _unwrap runs in multiprocessing workers, calls
connections.close_all() then close_old_connections() so forked children never
reuse the parent's PostgreSQL session (named server-side cursors / iterator
state); writes are done in the main process only.

Attributes:
  INSUFFICIENT_DATA_FOR_METRICS_PROCESSING: Attribute.
  METRICS_HOST_QUERY_BATCH: Attribute.
  METRICS_POOL_JOIN_TIMEOUT_S: Attribute.
  METRIC_NOT_COMPUTED_YET: Attribute.
  NO_GPU_AGGREGATE_TELEMETRY: Attribute.
  NO_SIMPLE_SAMPLES_MSG: Attribute.
  NO_TIME_SERIES_MSG: Attribute.
  NUMEXPR_MIN_ARRAY_SIZE: Attribute.
  _COMPLEX_NO_DATA_REASONS: Attribute.
  _COMPLEX_PLACEHOLDER_TYPE_UNITS: Attribute.
  _GPU_JOB_DETAIL_CATALOG: Attribute.
  _HOST_DATA_ROWS_MEMO_MAX_TIME_IN: Attribute.
  _MAX_FABRIC_BW_SANITY_MB_S: Attribute.
  _MAX_SANE_GPU_LINK_GBPS: Attribute.
  _MAX_SANE_PACKETRATE: Attribute.
  _PARENT_PERSIST_TIMEOUT_MARKERS: Attribute.
  _TIME_IMBALANCE_MAX_SLICE_RATIO: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import contextlib
import json
import os
import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.process_title import apply_pool_worker_process_title

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

from hpcperfstats.analysis.metrics.lib.gen import jid_table
from hpcperfstats.dbload.lib.multiprocessing_pool_health import abort_if_pool_workers_dead
from hpcperfstats.analysis.metrics.lib.gen.utils import utils
from hpcperfstats.lib.dcgm_blank import (
    is_dcgm_numeric_blank,
    nan_out_dcgm_numeric_blanks,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
    HOST_BLOCK_TYPE,
    HOST_CPU_HW_TYPE,
    HOST_CPU_TYPE,
    HOST_IB_TYPE,
    HOST_LNET_TYPE,
    HOST_MEM_TYPE,
    HOST_NUMA_TYPE,
    HOST_OPA_TYPE,
    INTEL_FP_ARITH_ALL_EVENTS,
    INTEL_FP_ARITH_DOUBLE_EVENTS,
    INTEL_FP_ARITH_SINGLE_EVENTS,
    INTEL_LEGACY_SSE_FLOP_EVENTS,
    LUSTRE_LLITE_TYPE,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    events_probe_names,
    event_probe_names_for_type,
    amd_df_type_names,
    amd_pmc_type_names,
    arm_dram_bw_event_names,
    arm_est_flops_event_names,
    arm_imc_types_probe_order,
    arm_int16_ops_event_names,
    arm_int8_ops_event_names,
    core_pmc_types_probe_order,
    dram_cas_read_write_pairs,
    fp_ops_retired_event_names,
    grace_fp_scalar_double_event_names,
    grace_fp_scalar_single_event_names,
    hbm_cas_read_write_pairs,
    host_cpu_hw_type_names,
    imc_types_probe_order,
    resolve_get_type,
    type_probe_names,
)
from hpcperfstats.analysis.metrics.lib.gen.imc_cas_bw import combine_cas_bw_scalars
from hpcperfstats.site.lib.machine.models import host_data, job_data, metrics_data

from hpcperfstats.analysis.metrics.lib.job_detail_fsio import (
    compute_job_detail_fsio_metric_rows,
    fsio_job_detail_catalog,
)
from hpcperfstats.analysis.metrics.lib.llite_metadata_iops_events import (
    LLITE_METADATA_IOPS_EVENTS,
)
from hpcperfstats.analysis.metrics.lib.db_retry import run_with_db_retry
from hpcperfstats.dbload.lib.db_unavailable import DatabaseUnavailableExit

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
  """
  Raised when ``Metrics.run`` makes no worker-result progress for too long.
  
  Attributes:
    partial_outcomes: Attribute.
    pending_jobs: Attribute.
    pool_reset_confirmed: Attribute.
    stalled_for_s: Attribute.
  """

  def __init__(
    self,
    stalled_for_s: Any,
    message: Any,
    pool_reset_confirmed: bool = False,
    *,
    partial_outcomes: Any | None = None,
    pending_jobs: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      stalled_for_s (Any): Stalled for s passed to this helper.
      message (Any): Message passed to this helper.
      pool_reset_confirmed (bool): Boolean flag for pool reset confirmed.
      partial_outcomes (Any | None): One of ``Any``, ``None``.
      pending_jobs (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> MetricsRunWorkerStallError(None, None, True, None, None)
    """
    super().__init__(message)
    self.stalled_for_s = float(stalled_for_s)
    self.pool_reset_confirmed = bool(pool_reset_confirmed)
    self.partial_outcomes = list(partial_outcomes or [])
    self.pending_jobs = list(pending_jobs or [])


class MetricsComputeJobTimeoutError(TimeoutError):
  """
  One metrics worker exceeded the per-job compute wall clock.
  """


def _metrics_jid_value(job_or_pk: Any) -> Any:
  """
  Stable jid string from either a job-like object or a raw primary key.
  
  Args:
    job_or_pk (Any): Job or pk passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _metrics_jid_value(None)  # doctest: +SKIP
  """
  return _coerce_metrics_identity_str(getattr(job_or_pk, "jid", job_or_pk))


def _metrics_run_outcome(
  jid: Any,
  *,
  ok: Any,
  status: Any,
  persisted_rows: int = 0,
  distinct_time_count: Any | None = None,
  persist_s: float = 0.0,
  error_type: Any | None = None,
  error_message: Any | None = None,
) -> Any:
  """
  Canonical per-jid outcome emitted by ``Metrics.run``.
  
  Args:
    jid (Any): Jid passed to this helper.
    ok (Any): Ok passed to this helper.
    status (Any): Status passed to this helper.
    persisted_rows (int): Integer value for persisted rows.
    distinct_time_count (Any | None): One of ``Any``, ``None``.
    persist_s (float): Floating-point value for persist s.
    error_type (Any | None): One of ``Any``, ``None``.
    error_message (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _metrics_run_outcome(None, None, None, 0, None, 0, None, None)
  """
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


def _is_parent_persist_timeout_error(exc: Any) -> Any:
  """
  Best-effort classification for bounded persist timeout/lock timeout failures.
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_parent_persist_timeout_error(None)  # doctest: +SKIP
  """
  text = str(exc or "").lower()
  return any(marker in text for marker in _PARENT_PERSIST_TIMEOUT_MARKERS)


def _wait_pool_processes_bounded(active_pool: Any, timeout_s: Any) -> Any:
  """
  Wait up to ``timeout_s`` for pool worker processes to exit.
  
  Args:
    active_pool (Any): Active pool passed to this helper.
    timeout_s (Any): Timeout s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _wait_pool_processes_bounded(None, None)  # doctest: +SKIP
  """
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


def _close_pool_bounded(active_pool: Any, timeout_s: Any) -> Any:
  """
  Best-effort bounded graceful close; terminates if workers linger.
  
  Args:
    active_pool (Any): Active pool passed to this helper.
    timeout_s (Any): Timeout s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _close_pool_bounded(None, None)  # doctest: +SKIP
  """
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


def _terminate_pool_bounded(active_pool: Any, timeout_s: Any) -> Any:
  """
  Best-effort bounded terminate used in stall recovery paths.
  
  Args:
    active_pool (Any): Active pool passed to this helper.
    timeout_s (Any): Timeout s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _terminate_pool_bounded(None, None)  # doctest: +SKIP
  """
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


def _log_exception_details(prefix: Any, exc: Any) -> None:
  """
  Emit type/repr and traceback lines for diagnostics-first scheduler logs.
  
  Args:
    prefix (Any): Prefix passed to this helper.
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    None
  
  Examples:
    >>> _log_exception_details(None, None)  # doctest: +SKIP
  """
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


def _coerce_metrics_identity_str(value: Any) -> Any:
  """
  Stable string for metrics_data keys and set/hash uses (never lists/dicts raw).
  
  Bad monitor/ingest payloads occasionally surface list-typed labels in
    host_data
  or schema-derived paths; using those in ``set`` membership, ``frozenset``, or
  ORM dedupe keys raises ``unhashable type: 'list'``.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _coerce_metrics_identity_str(None)  # doctest: +SKIP
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


def _hashable_metric_events_signature(events: Any) -> Any:
  """
  Tuple of stable strings for ``simple_metric_cache`` / ``rows_cache`` dict.
  
    keys.
  
  ``tuple(events)`` is unsafe when ingest/catalog corruption nests lists inside
  ``events`` — the tuple can contain a raw ``list``, which is unhashable and
  crashes ``cache_key in cache`` during ``job_arc`` / ``job_value_mean``.
  
  Args:
    events (Any): Events passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _hashable_metric_events_signature(None)  # doctest: +SKIP
  """
  if not events:
    return ()
  return tuple(_coerce_metrics_identity_str(e) for e in events)


def _flatten_event_names_for_host_data_query(
  events: Any,
  typ: Any | None = None,
) -> Any:
  """
  Expand nested sequences and legacy event aliases for ``event__in`` queries.
  
  Args:
    events (Any): Events passed to this helper.
    typ (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _flatten_event_names_for_host_data_query(None, None)  # doctest: +SKIP
  """
  return events_probe_names(events, typ=typ)


def _sanitize_metrics_compute_rows(rows: Any) -> Any:
  """
  Normalize type/metric/units on every worker-produced row before persist.
  
  Args:
    rows (Any): Rows passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _sanitize_metrics_compute_rows(None)  # doctest: +SKIP
  """
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


def _finite_amax(values: Any, *, reject_dcgm_blank: bool = False) -> Any:
  """
  Return ``amax`` over finite entries, or ``None`` when none are finite.
  
  When ``reject_dcgm_blank`` is True, DCGM blank-family sentinels are excluded
  (GPU power / util / throttle gauges).
  
  Args:
    values (Any): Values passed to this helper.
    reject_dcgm_blank (bool): Boolean flag for reject dcgm blank.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _finite_amax(None, True)  # doctest: +SKIP
  """
  arr = np.asarray(values, dtype=np.float64)
  if arr.size == 0:
    return None
  if reject_dcgm_blank:
    arr = nan_out_dcgm_numeric_blanks(arr)
  fin = arr[np.isfinite(arr)]
  if fin.size == 0:
    return None
  return float(amax(fin))


def _resolve_metrics_run_per_job_timeout_s() -> Any:
  """
  Per-job compute wall clock in pool workers (0 INI/env →.
  
    ``metrics_run_stall_timeout_s``).
  
  Returns:
    Any: Open return polymorphism from
    ``_resolve_metrics_run_per_job_timeout_s``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> _resolve_metrics_run_per_job_timeout_s()  # doctest: +SKIP
  """
  per_job = float(cfg.get_metrics_run_per_job_timeout_s())
  if per_job > 0.0:
    return per_job
  return max(5.0, float(cfg.get_metrics_run_stall_timeout_s()))


def _run_compute_metrics_timed(
  metrics_obj: Any,
  job: Any,
  timeout_s: Any,
) -> Any:
  """
  Run ``compute_metrics`` with a Unix wall-clock cap (no-op when ``timeout_s <=.
  
    0``).
  
  Args:
    metrics_obj (Any): Metrics obj passed to this helper.
    job (Any): Job record (Django ``job_data`` or job-like mapping).
    timeout_s (Any): Timeout s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _run_compute_metrics_timed(None, None, None)  # doctest: +SKIP
  """
  timeout_s = float(timeout_s)
  if timeout_s <= 0.0:
    return metrics_obj.compute_metrics(job)
  if not hasattr(signal, "SIGALRM"):
    return metrics_obj.compute_metrics(job)

  jid = getattr(job, "jid", "?")

  def _handler(signum: Any, frame: Any) -> None:
    """
    Internal helper to handle handler.
    
    Args:
      signum (Any): Signum passed to this helper.
      frame (Any): Frame passed to this helper.
    
    Returns:
      None
    
    Raises:
      MetricsComputeJobTimeoutError: Raised when ``_handler`` hits a
      ``MetricsComputeJobTimeoutError`` failure path.
    
    Examples:
      >>> _handler(None, None)  # doctest: +SKIP
    """
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


def _coerced_metric_name_set(metric_names: Any) -> Any:
  """
  Return a hash-safe set of metric names from any iterable.
  
  Args:
    metric_names (Any): Metric names passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _coerced_metric_name_set(None)  # doctest: +SKIP
  """
  out = set()
  for name in metric_names or ():
    out.add(_coerce_metrics_identity_str(name))
  return out


# Skip time_imbalance slices where b/a is non-finite or absurd (near-zero "before"
# integral blows up the ratio); values above this are not meaningful as %.
_TIME_IMBALANCE_MAX_SLICE_RATIO = 1e9


def _time_imbalance_min_ratio_for_rate(rate: Any, tmid: Any) -> Any:
  """
  Minimum after/before mean CPU-rate ratio over mid timeline splits.
  
  Matches the historical ``time_imbalance`` loop (same split set, windows, and
  clamps) but uses prefix trapezoid segments so cost is ``O(n)`` in
  ``n = len(rate)`` instead of ``O(n^2)`` repeated ``trapz`` calls.
  
  ``rate`` / ``tmid`` are per-interval series (length ``nt - 1``). Returns the
  minimum finite ratio, or ``None`` when no slice qualifies.
  
  Args:
    rate (Any): Rate passed to this helper.
    tmid (Any): Tmid passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _time_imbalance_min_ratio_for_rate(None, None)  # doctest: +SKIP
  """
  rate = np.asarray(rate, dtype=np.float64)
  tmid = np.asarray(tmid, dtype=np.float64)
  n = int(rate.shape[0])
  if n < 4 or tmid.shape[0] != n:
    return None
  # Segment contributions between consecutive midpoint samples (numpy trapz).
  seg = 0.5 * (rate[:-1] + rate[1:]) * (tmid[1:] - tmid[:-1])
  # cum[k] == sum(seg[:k]) == trapz(rate[:k+1], tmid[:k+1]) for k>=1 with
  # cum[0]==0; trapz(rate[:i], tmid[:i]) == cum[i-1]; trapz(rate[i:], tmid[i:])
  # == total - cum[i] (excludes the segment that straddles i-1 -> i).
  cum = np.empty(n, dtype=np.float64)
  cum[0] = 0.0
  if n > 1:
    np.cumsum(seg, out=cum[1:])
  total = float(cum[n - 1])
  min_ratio = None
  # Historical loop: i in {2 .. nt-3} with nt = n+1 => i in {2 .. n-2}.
  for i in range(2, n - 1):
    before_window = float(tmid[i] - tmid[0])
    after_window = float(tmid[-1] - tmid[i])
    if before_window <= 0 or after_window <= 0:
      continue
    a = float(cum[i - 1]) / before_window
    if not (a > 0) or not np.isfinite(a):
      continue
    b = (total - float(cum[i])) / after_window
    if not np.isfinite(b):
      continue
    ratio = b / a
    if not np.isfinite(ratio) or ratio < 0:
      continue
    if ratio > _TIME_IMBALANCE_MAX_SLICE_RATIO:
      continue
    if min_ratio is None or ratio < min_ratio:
      min_ratio = ratio
  return min_ratio


def _add_arrays(a: Any, b: Any) -> Any:
  """
  Fast path for a+b on large arrays.
  
  Args:
    a (Any): A passed to this helper.
    b (Any): B passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _add_arrays(None, None)  # doctest: +SKIP
  """
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
    "job_cpu_gpu_watt_hours": ("job", "Wh"),
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
    "job_cpu_gpu_watt_hours": (
        "No usable CPU power estimate for job energy (watt-hours)"
    ),
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


def _per_interval_rate(values: Any, t: Any) -> Any:
  """
  Compute diff(values) / diff(t) without divide-by-zero.
  
  Sample pairs with non-positive delta-t (duplicate timestamps) yield NaN so
  callers can use nan-aware reductions or substitute zeros for integration.
  
  Args:
    values (Any): Values passed to this helper.
    t (Any): T passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _per_interval_rate(None, None)  # doctest: +SKIP
  """
  dy = np.asarray(diff(values), dtype=np.float64)
  dt = np.asarray(diff(np.asarray(t, dtype=np.float64)), dtype=np.float64)
  out = np.full(dy.shape, np.nan, dtype=np.float64)
  np.divide(dy, dt, out=out, where=dt > 0)
  return out


# Reject counter-wrap poison (~2^63/dt or ~2^64/dt). Ceilings are far above any
# physical fabric/GPU-link rate but well below uint64-wrap /1e9 display poison.
_MAX_SANE_PACKETRATE = 1.0e10  # packets/s
_MAX_SANE_GPU_LINK_GBPS = 1.0e5  # GB/s


def _sane_peak_from_rates(
  rates: Any,
  *,
  divisor: float = 1.0,
  max_sane: Any | None = None,
) -> Any:
  """
  Return max positive finite rate after optional physical ceiling, else None.
  
  Args:
    rates (Any): Rates passed to this helper.
    divisor (float): Floating-point value for divisor.
    max_sane (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _sane_peak_from_rates(None, 0, None)  # doctest: +SKIP
  """
  fin = np.asarray(rates, dtype=np.float64)
  fin = fin[np.isfinite(fin)]
  if fin.size == 0:
    return None
  div = float(divisor) if divisor else 1.0
  scaled = fin / div
  positive = scaled[scaled > 0]
  if positive.size == 0:
    return None
  if max_sane is not None:
    positive = positive[positive <= float(max_sane)]
    if positive.size == 0:
      return None
  return float(positive.max())


def _peak_from_cluster_arc(
  u: Any,
  typename: Any,
  column_indices: Any,
  divisor: Any,
  max_sane: Any | None = None,
) -> Any:
  """
  Peak of host-averaged ingest ``arc`` (already a rate) when available.
  
  Args:
    u (Any): U passed to this helper.
    typename (Any): Typename passed to this helper.
    column_indices (Any): Column indices passed to this helper.
    divisor (Any): Divisor passed to this helper.
    max_sane (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _peak_from_cluster_arc(None, None, None, None, None)  # doctest: +SKIP
  """
  cmap = getattr(u.job, "cluster_mean_arc_by_type", None) or {}
  cm = cmap.get(typename)
  if cm is None or cm.size == 0:
    return None
  s = np.zeros(cm.shape[0], dtype=np.float64)
  for j in column_indices:
    if j < 0 or j >= cm.shape[1]:
      return None
    s = s + cm[:, j]
  return _sane_peak_from_rates(s, divisor=divisor, max_sane=max_sane)


def _peak_interval_rate_from_cluster_mean(
  u: Any,
  typename: Any,
  column_indices: Any,
  divisor: Any,
  max_sane: Any | None = None,
) -> Any:
  """
  Peak rate from cluster means: prefer ``arc``, else dy/dt on ``value``.
  
  Uses ``job.cluster_mean_arc_by_type`` / ``cluster_mean_by_type`` (see
  ``_JobForMetrics``). ``max_sane`` is in the same units as the returned peak
  (after ``divisor``). Returns None when only wrap-class poison remains.
  
  Args:
    u (Any): U passed to this helper.
    typename (Any): Typename passed to this helper.
    column_indices (Any): Column indices passed to this helper.
    divisor (Any): Divisor passed to this helper.
    max_sane (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _peak_interval_rate_from_cluster_mean(None, None, None, None, None)
  """
  arc_peak = _peak_from_cluster_arc(
      u, typename, column_indices, divisor, max_sane=max_sane)
  if arc_peak is not None:
    return arc_peak
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
  return _sane_peak_from_rates(ratio, divisor=divisor, max_sane=max_sane)


class _EventIndex:
  """
  Holds the integer index of an event in a schema. Used by _Schema.__getitem__.
  
  Attributes:
    index: Attribute.
  """

  def __init__(self, index: int) -> None:
    """
    Store the integer index for an event.
    
    Args:
      index (int): Integer value for index.
    
    Returns:
      None
    
    Examples:
      >>> _EventIndex(0)  # doctest: +SKIP
    """
    self.index = index


class _Schema:
  """
  Schema for a type: list of event names and a name->index mapping.
  
  Attributes:
    _index: Attribute.
    desc: Attribute.
    events: Attribute.
  """

  def __init__(self, events: Any) -> None:
    """
    Build event list and name->index mapping from event names.
    
    Args:
      events (Any): Events passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> _Schema(None)  # doctest: +SKIP
    """
    # Normalise event names to strings so that schema construction is robust
    # when upstream code passes non‑string labels (e.g. pandas.Timestamp).
    self.events = [str(e) for e in events]
    self._index = {name: idx for idx, name in enumerate(self.events)}
    self.desc = " ".join(self.events) + "\n"

  def __getitem__(self, name: Any) -> Any:
    """
    Return _EventIndex for the given event name.
    
    Args:
      name (Any): Name passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> __getitem__(None)  # doctest: +SKIP
    """
    return _EventIndex(self._index[name])

  def __contains__(self, name: Any) -> Any:
    """
    Membership check for event columns (partial schemas must not KeyError.
    
      complex metrics).
    
    Args:
      name (Any): Name passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> __contains__(None)  # doctest: +SKIP
    """
    return str(name) in self._index

  def __iter__(self) -> Any:
    """
    Iterate event names (required: without this, ``for x in schema`` uses.
    
      integer indices and breaks __getitem__).
    
    Returns:
      Any: Open return polymorphism from ``__iter__``: concrete type depends
      on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __iter__()  # doctest: +SKIP
    """
    return iter(self.events)


def _metric_type_events_feasible(schema: Any, typ: Any, events: Any) -> Any:
  """
  Return False when ``jt.schema`` is known and no requested event exists for.
  
    typ.
  
  Same contract as SummaryPlot ``_summary_type_events_feasible``: empty/unknown
  schema allows ORM; populated schema skips impossible type/event probes so
  cascading ``job_arc`` helpers do not burn the per-job wall clock on empty
  ``host_data`` scans (production: MetricsComputeJobTimeoutError in list(qs)).
  
  Args:
    schema (Any): Schema passed to this helper.
    typ (Any): Typ passed to this helper.
    events (Any): Events passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _metric_type_events_feasible(None, None, None)  # doctest: +SKIP
  """
  if not isinstance(schema, dict) or not schema:
    return True
  if not events:
    return any(t in schema for t in type_probe_names(typ))
  probed_events = set(events_probe_names(list(events), typ=typ))
  for t in type_probe_names(typ):
    if t not in schema:
      continue
    present = schema[t]
    if probed_events.intersection(present):
      return True
  return False


def _schema_has_events(schema: Any, *event_names: Any) -> Any:
  """
  True when ``schema`` defines every listed event (handles incomplete.
  
    ``mem``/fabric/net rows).
  
  Args:
    schema (Any): Schema passed to this helper.
    *event_names (Any): Extra positional values for ``event_names``; element
    types match the helper's documented protocol.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _schema_has_events(None)  # doctest: +SKIP
  """
  if schema is None:
    return False
  return all(name in schema for name in event_names)


def _schema_has_events_for_type(
  schema: Any,
  typ: Any,
  *event_names: Any,
) -> Any:
  """
  True when each event resolves via type-scoped dual-read into ``schema``.
  
  Args:
    schema (Any): Schema passed to this helper.
    typ (Any): Typ passed to this helper.
    *event_names (Any): Extra positional values for ``event_names``; element
    types match the helper's documented protocol.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _schema_has_events_for_type(None, None)  # doctest: +SKIP
  """
  if schema is None:
    return False
  for name in event_names:
    if not any(p in schema for p in event_probe_names_for_type(typ, name)):
      return False
  return True


def _schema_event_index(schema: Any, typ: Any, event_name: Any) -> Any:
  """
  Column index for ``event_name`` under ``typ``, preferring canonical probe.
  
    order.
  
  Args:
    schema (Any): Schema passed to this helper.
    typ (Any): Typ passed to this helper.
    event_name (Any): Event name passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    KeyError: Raised when ``_schema_event_index`` hits a ``KeyError`` failure
    path.
  
  Examples:
    >>> _schema_event_index(None, None, None)  # doctest: +SKIP
  """
  for probe in event_probe_names_for_type(typ, event_name):
    if probe in schema:
      return schema[probe].index
  raise KeyError(event_name)


class _Host:
  """
  Minimal host container with a stats dict (typename -> dev -> array).
  
  Attributes:
    stats: Attribute.
  """

  def __init__(self) -> None:
    """
    Initialize empty stats dict.
    
    Returns:
      None
    
    Examples:
      >>> _Host()  # doctest: +SKIP
    """
    self.stats = {}


def _fqdn_hosts_for_job_model(job: Any) -> Any:
  """
  Internal helper to handle fqdn hosts for job model.
  
  Args:
    job (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _fqdn_hosts_for_job_model(None)  # doctest: +SKIP
  """
  suffix = "." + cfg.get_host_name_ext()
  hosts = []
  for host in (job.host_list or []):
    h = str(host or "").strip()
    if not h:
      continue
    hosts.append(h if "." in h else (h + suffix))
  return hosts


def _in_window_telemetry_bounds_for_job(job: Any) -> Any:
  """
  Return ``(telemetry_first_time, telemetry_last_time)`` for accounting hosts.
  
  Args:
    job (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _in_window_telemetry_bounds_for_job(None)  # doctest: +SKIP
  """
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
  tkw = {"time__gte": start_time, "time__lte": end_time}
  slice_s = int(cfg.get_metrics_plot_aggregate_time_slice_s())
  overall_mn = None
  overall_mx = None

  def run(hosts_list: Any, tf_cur: Any) -> Any:
    """
    Min/Max telemetry time for one host×time chunk.

    Args:
      hosts_list (Any): Hostnames for this attempt.
      tf_cur (Any): Time filter dict.

    Returns:
      Any: Aggregate dict with ``mn`` / ``mx``.

    Examples:
      >>> True
      True
    """
    return (
        host_data.objects.filter(
            host__in=hosts_list,
            **(tf_cur or {}),
        ).aggregate(mn=Min("time"), mx=Max("time"))
    )

  def merge(left: Any, right: Any) -> Any:
    """
    Merge two Min/Max aggregate dicts.

    Args:
      left (Any): Left aggregate.
      right (Any): Right aggregate.

    Returns:
      Any: Combined ``mn`` / ``mx`` dict.

    Examples:
      >>> True
      True
    """
    left = left or {}
    right = right or {}
    mn_vals = [v for v in (left.get("mn"), right.get("mn")) if v is not None]
    mx_vals = [v for v in (left.get("mx"), right.get("mx")) if v is not None]
    return {
        "mn": min(mn_vals) if mn_vals else None,
        "mx": max(mx_vals) if mx_vals else None,
    }

  for host_chunk, tf in jid_table._iter_host_time_query_chunks(
      hosts,
      tkw,
      batch_size=METRICS_HOST_QUERY_BATCH,
      slice_s=slice_s,
  ):
    part = jid_table._run_with_host_time_timeout_retry(
        host_chunk,
        tf,
        run,
        merge,
        empty={"mn": None, "mx": None},
    )
    folded = merge({"mn": overall_mn, "mx": overall_mx}, part)
    overall_mn = folded.get("mn")
    overall_mx = folded.get("mx")
  return overall_mn, overall_mx


class _JobForMetrics:
  """
  Minimal job-like object compatible with
    hpcperfstats.analysis.metrics.lib.gen.utils.utils. Built from jid_table full
    host_data DataFrame.
  
  Attributes:
    acct: Attribute.
    cluster_mean_arc_by_type: Attribute.
    cluster_mean_by_type: Attribute.
    hosts: Attribute.
    jid: Attribute.
    per_host_distinct_time_sum: Attribute.
    schemas: Attribute.
    times: Attribute.
  """

  def __init__(self, jt: Any) -> None:
    """
    Build job-like view from jid_table full host_data DataFrame.
    
    Args:
      jt (Any): Jt passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> _JobForMetrics(None)  # doctest: +SKIP
    """
    self.jid = jt.jid
    self.hosts = {}
    self.schemas = {}
    # Per-typename (n_times, n_events): mean of `value` across hosts at each
    # global timestamp (for peak interval-rate metrics; avoids bogus diffs when
    # nodes share a time axis but sparse samples per host).
    self.cluster_mean_by_type = {}
    # Same shape as cluster_mean_by_type but from ingest ``arc`` (already rates).
    self.cluster_mean_arc_by_type = {}
    self.acct = {"cores": 1, "nodes": 1}

    df = jt.get_full_host_data_df(
        columns=["host", "time", "type", "event", "value", "arc"])
    # If there is no time information, we cannot build a valid time axis; treat
    # as no data for this job (avoids KeyError when sorting by missing column).
    if df.empty or "time" not in df.columns:
      self.times = np.array([])
      self.per_host_distinct_time_sum = 0
      self.cluster_mean_by_type = {}
      self.cluster_mean_arc_by_type = {}
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
      if "arc" in type_df.columns:
        try:
          arc_pivot = (
              type_df[["time", "event", "arc"]]
              .groupby(["time", "event"])["arc"]
              .mean()
              .unstack(fill_value=np.nan)
          )
          arc_pivot = arc_pivot.reindex(
              index=times, fill_value=np.nan
          ).reindex(columns=events, fill_value=np.nan)
          self.cluster_mean_arc_by_type[typename] = np.ascontiguousarray(
              arc_pivot.values, dtype=np.float64
          )
        except (ValueError, KeyError):
          self.cluster_mean_arc_by_type[typename] = np.full(
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


@contextlib.contextmanager
def _pg_session_statement_timeout_for_metrics_worker() -> Iterator[Any]:
  """
  Apply ``metrics_worker_statement_timeout_ms`` for pool compute, then restore.
  
  Default ``0`` disables PostgreSQL ``statement_timeout`` for the compute window
  so legitimate multi-host ``host_data`` scans are not canceled at the web/API
  120s session limit. Per-job SIGALRM remains the wall-clock backstop. Restore
  is best-effort (swallow OperationalError/DatabaseError on dead connections).
  
  Yields:
    Iterator[Any]: Open return polymorphism from
    ``_pg_session_statement_timeout_for_metrics_worker``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> _pg_session_statement_timeout_for_metrics_worker()  # doctest: +SKIP
  """
  try:
    conn = connections["default"]
  except Exception:
    yield
    return
  if getattr(conn, "vendor", None) != "postgresql":
    yield
    return
  timeout_ms = int(cfg.get_metrics_worker_statement_timeout_ms())
  restore_ms = cfg.get_db_statement_timeout_ms()
  with conn.cursor() as cursor:
    if timeout_ms <= 0:
      cursor.execute("SET statement_timeout = 0")
    else:
      cursor.execute("SET statement_timeout = %s", [timeout_ms])
  try:
    yield
  finally:
    try:
      with conn.cursor() as cursor:
        if restore_ms > 0:
          cursor.execute("SET statement_timeout = %s", [restore_ms])
        else:
          cursor.execute("SET statement_timeout = 0")
    except (OperationalError, DatabaseError):
      pass


def _unwrap(args: Any) -> Any:
  """
  Wrapper for pool: call compute_metrics on the job. Used by Metrics.run.
  
  Args:
    args (Any): Args passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_unwrap`` hits a ``Exception`` failure path.
  
  Examples:
    >>> _unwrap(None)  # doctest: +SKIP
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

    def _compute() -> Any:
      """
      Internal helper to compute.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> _compute()  # doctest: +SKIP
      """
      with _pg_session_statement_timeout_for_metrics_worker():
        return _run_compute_metrics_timed(
            metrics_obj, job, per_job_timeout_s)

    payload = run_with_db_retry(_compute, attempts=2)
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
  finally:
    from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
        release_spawn_pool_worker_memory,
    )

    release_spawn_pool_worker_memory()


def _persist_metrics_batch(
  job_results: Any,
  distinct_time_count: int,
  telemetry_first_time: Any | None = None,
  telemetry_last_time: Any | None = None,
) -> None:
  """
  Upsert metrics_data rows for job_results; set.
  
    job_data.metrics_distinct_time_count.
  
  Uses bulk_create(..., update_conflicts=...) so we do not rely on INSERT
    RETURNING
  row-count matching (Django asserts that for plain bulk_create on PostgreSQL;
  some stacks violate it). Dedupes (jid, type, metric) within the batch so
  ON CONFLICT does not hit the same row twice.
  Called in main process only.
  
  Args:
    job_results (Any): Job results passed to this helper.
    distinct_time_count (int): Integer value for distinct time count.
    telemetry_first_time (Any | None): One of ``Any``, ``None``.
    telemetry_last_time (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``_persist_metrics_batch`` hits a ``Exception``
    failure path.
  
  Examples:
    >>> _persist_metrics_batch(None, 0, None, None)  # doctest: +SKIP
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
      from django.core.cache import cache as _job_detail_cache
      from hpcperfstats.site.lib.machine.cache_utils import (
          invalidate_metrics_distinct_cache,
          make_job_detail_cache_key,
      )

      invalidate_metrics_distinct_cache()
      for jid in jids:
        if jid:
          _job_detail_cache.delete(make_job_detail_cache_key(jid))
    except Exception:
      pass


def _persist_metrics_payload(payload: Any) -> Any:
  """
  Persist one worker payload and return a truthful per-jid outcome.
  
  Args:
    payload (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _persist_metrics_payload(None)  # doctest: +SKIP
  """
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
  active_pool: Any,
  tasks: Any,
  chunksize: Any,
  *,
  poll_timeout_s: Any,
  stall_timeout_s: Any,
) -> Any:
  """
  Apply ``imap_unordered`` results from workers and persist metrics.
  
  ``imap_unordered`` can block forever when a worker wedges (driver deadlock,
  query hang, C-extension lock). Poll with timeout and fail fast on prolonged
  no-progress so scheduler code can recover the pool and continue.
  
  Args:
    active_pool (Any): Active pool passed to this helper.
    tasks (Any): Task payload for a worker (tuple/list per this helper's
    protocol).
    chunksize (Any): Chunksize passed to this helper.
    poll_timeout_s (Any): Poll timeout s passed to this helper.
    stall_timeout_s (Any): Stall timeout s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_drain_metrics_imap`` hits a ``Exception``
    failure path.
    MetricsRunWorkerStallError: Raised when ``_drain_metrics_imap`` hits a
    ``MetricsRunWorkerStallError`` failure path.
  
  Examples:
    >>> _drain_metrics_imap(None, None, None, None, None)  # doctest: +SKIP
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


def _jid_table_host_data_time_kwargs(base: Any) -> Any:
  """
  ORM time scope from ``jid_table._base_filter`` (full window or sampled.
  
    ``time__in``).
  
  Args:
    base (Any): Base passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _jid_table_host_data_time_kwargs(None)  # doctest: +SKIP
  """
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


def _host_data_row_cache_key(
  tkw: Any,
  typename: Any,
  events: Any,
  metric_column: Any,
  *,
  sum_per_sample: bool = False,
  nonnegative_only: bool = False,
) -> Any:
  """
  Hashable key for one batched host_data fetch within a single.
  
    ``compute_metrics`` pass.
  
  Args:
    tkw (Any): Tkw passed to this helper.
    typename (Any): Typename passed to this helper.
    events (Any): Events passed to this helper.
    metric_column (Any): Metric column passed to this helper.
    sum_per_sample (bool): Boolean flag for sum per sample.
    nonnegative_only (bool): Boolean flag for nonnegative only.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _host_data_row_cache_key(None, None, None, None, True, True)
  """
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
  return (
      typename,
      metric_column,
      bool(sum_per_sample),
      bool(nonnegative_only),
      _hashable_metric_events_signature(events),
      t_part,
  )


def _host_data_metric_rows_queryset(
  hosts: Any,
  tkw: Any,
  typename: Any,
  events: Any,
  metric_column: Any,
  *,
  sum_per_sample: bool = False,
  nonnegative_only: bool = False,
) -> Any:
  """
  Rows for metric bucketing: raw samples, or one SQL-summed row per (host,.
  
    time).
  
  ``sum_per_sample`` moves the per-sample total across events and devices into
  PostgreSQL, so a job with many events/devices transfers one row per sample
    time
  instead of ``events × devices`` rows. The aggregate queryset labels its
    columns
  with the ``jid_table`` aliases; ``_normalize_host_data_metric_rows`` maps them
  back so both paths yield ``{host, time, <metric_column>}`` rows.
  
  Args:
    hosts (Any): Hosts passed to this helper.
    tkw (Any): Tkw passed to this helper.
    typename (Any): Typename passed to this helper.
    events (Any): Events passed to this helper.
    metric_column (Any): Metric column passed to this helper.
    sum_per_sample (bool): Boolean flag for sum per sample.
    nonnegative_only (bool): Boolean flag for nonnegative only.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _host_data_metric_rows_queryset(0)  # doctest: +SKIP
  """
  from hpcperfstats.site.lib.machine.models import host_data

  qs = host_data.objects.filter(
      **tkw,
      host__in=hosts,
      type=typename,
      event__in=events,
  )
  if sum_per_sample:
    return jid_table.host_data_sum_val_per_sample_queryset(
        qs, metric_column, nonnegative_only=nonnegative_only)
  return qs.values("host", "time", metric_column).order_by("host", "time")


def _normalize_host_data_metric_rows(rows: Any, metric_column: Any) -> Any:
  """
  Relabel SQL-aggregate rows to the raw-fetch shape ``{host, time, column}``.
  
  Args:
    rows (Any): Rows passed to this helper.
    metric_column (Any): Metric column passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_host_data_metric_rows(None, None)  # doctest: +SKIP
  """
  time_alias = jid_table.HOST_DATA_TIME_ALIAS
  sum_alias = jid_table.HOST_DATA_SUM_VAL_ALIAS
  return [
      {
          "host": row["host"],
          "time": row[time_alias],
          metric_column: row[sum_alias],
      }
      for row in rows
  ]


def _host_data_metric_rows_with_host_chunk_retry(
  host_chunk: Any,
  tkw: Any,
  typename: Any,
  events: Any,
  metric_column: Any,
  *,
  min_hosts: int = 1,
  max_attempts: int = 2,
  sum_per_sample: bool = False,
  nonnegative_only: bool = False,
) -> Any:
  """
  Materialize metric ``values()`` rows; split hosts or retry on statement.
  
    timeout.
  
  Mirrors ``jid_table._queryset_to_dataframe_with_host_chunk_retry`` for the
  list-of-dicts path used by metric bucketing.
  
  Args:
    host_chunk (Any): Host chunk passed to this helper.
    tkw (Any): Tkw passed to this helper.
    typename (Any): Typename passed to this helper.
    events (Any): Events passed to this helper.
    metric_column (Any): Metric column passed to this helper.
    min_hosts (int): Integer value for min hosts.
    max_attempts (int): Integer value for max attempts.
    sum_per_sample (bool): Boolean flag for sum per sample.
    nonnegative_only (bool): Boolean flag for nonnegative only.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_host_data_metric_rows_with_host_chunk_retry``
    hits a ``Exception`` failure path.
    last_exc: Raised when ``_host_data_metric_rows_with_host_chunk_retry``
    hits a ``last_exc`` failure path.
  
  Examples:
    >>> _host_data_metric_rows_with_host_chunk_retry(0)  # doctest: +SKIP
  """
  hosts = [str(h) for h in host_chunk if h]
  if not hosts:
    return []
  last_exc = None
  for attempt in range(max_attempts):
    try:
      close_old_connections()
      rows = list(
          _host_data_metric_rows_queryset(
              hosts,
              tkw,
              typename,
              events,
              metric_column,
              sum_per_sample=sum_per_sample,
              nonnegative_only=nonnegative_only,
          )
      )
      if sum_per_sample:
        return _normalize_host_data_metric_rows(rows, metric_column)
      return rows
    except OperationalError as exc:
      last_exc = exc
      if not jid_table._is_statement_timeout_error(exc):
        raise
      if len(hosts) > min_hosts:
        mid = max(1, len(hosts) // 2)
        left = _host_data_metric_rows_with_host_chunk_retry(
            hosts[:mid],
            tkw,
            typename,
            events,
            metric_column,
            min_hosts=min_hosts,
            max_attempts=max_attempts,
            sum_per_sample=sum_per_sample,
            nonnegative_only=nonnegative_only,
        )
        right = _host_data_metric_rows_with_host_chunk_retry(
            hosts[mid:],
            tkw,
            typename,
            events,
            metric_column,
            min_hosts=min_hosts,
            max_attempts=max_attempts,
            sum_per_sample=sum_per_sample,
            nonnegative_only=nonnegative_only,
        )
        return left + right
      if attempt + 1 >= max_attempts:
        raise
      close_old_connections()
  if last_exc is not None:
    raise last_exc
  return []


# Prefer host×time chunks sized like jid_table's default (16) so ~48-host jobs
# issue several bounded queries; pairs with metrics_worker_statement_timeout_ms
# so chunk-on-timeout split can fire before the 900s SIGALRM. Must stay equal to
# ``jid_table.JID_TABLE_HOST_QUERY_BATCH`` (drift-tested).
METRICS_HOST_QUERY_BATCH = 16


def _host_data_metric_rows_batched(
  tkw: Any,
  hosts: Any,
  typename: Any,
  events: Any,
  metric_column: Any,
  rows_cache: Any | None = None,
  *,
  sum_per_sample: bool = False,
  nonnegative_only: bool = False,
) -> Any:
  """
  Fetch host_data rows for metrics bucketing via host×time chunks.

  Rows are always ``{host, time, <metric_column>}``. With ``sum_per_sample`` the
  value is the per-sample total across events and devices, summed by PostgreSQL;
  host×time chunks are disjoint and each chunk groups per (host, time), so rows
  stay unique per (host, time) and callers can skip a pandas groupby.

  Args:
    tkw (Any): Time-filter kwargs for the job window.
    hosts (Any): Hostnames for this job.
    typename (Any): ``host_data.type`` string.
    events (Any): Event name(s) for the metric probe.
    metric_column (Any): Column name (``arc`` / ``value`` / ``delta``).
    rows_cache (Any | None): Optional dict cache keyed by probe identity.
    sum_per_sample (bool): When True, SQL-SUM per (host, sample time).
    nonnegative_only (bool): When True, SUM only nonnegative samples.

  Returns:
    Any: List of row dicts (possibly empty).

  Examples:
    >>> _host_data_metric_rows_batched(0)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg

  host_list = list(hosts)
  if not host_list:
    return []
  cache_key = None
  if rows_cache is not None:
    cache_key = _host_data_row_cache_key(
        tkw,
        typename,
        events,
        metric_column,
        sum_per_sample=sum_per_sample,
        nonnegative_only=nonnegative_only,
    )
    if cache_key is not None and cache_key in rows_cache:
      return rows_cache[cache_key]
  batch = jid_table._coerce_jid_table_host_query_batch_size(
      METRICS_HOST_QUERY_BATCH)
  ev = _flatten_event_names_for_host_data_query(events, typ=typename)
  slice_s = int(cfg.get_metrics_plot_aggregate_time_slice_s())
  rows: list = []

  def run(hosts_list: Any, tf_cur: Any) -> Any:
    """
    Materialize metric rows for one host×time chunk.

    Args:
      hosts_list (Any): Hostnames for this attempt.
      tf_cur (Any): Time filter dict.

    Returns:
      Any: List of row dicts.

    Examples:
      >>> True
      True
    """
    return _host_data_metric_rows_with_host_chunk_retry(
        hosts_list,
        tf_cur or {},
        typename,
        ev,
        metric_column,
        sum_per_sample=sum_per_sample,
        nonnegative_only=nonnegative_only,
    )

  for host_chunk, tf in jid_table._iter_host_time_query_chunks(
      host_list,
      tkw,
      batch_size=batch,
      slice_s=slice_s,
  ):
    rows.extend(
        jid_table._run_with_host_time_timeout_retry(
            host_chunk,
            tf,
            run,
            jid_table._merge_list_results,
            empty=[],
        )
    )
  if rows_cache is not None and cache_key is not None:
    rows_cache[cache_key] = rows
  return rows


def _drop_first_bucket_per_host_if_safe(grouped: Any) -> Any:
  """
  Drop the first 5m bucket per host only when a later bucket remains.
  
  Short jobs that land in a single bucket must keep that sample; otherwise
  ``job_arc`` / ``job_value_mean`` return None even when host_data exists.
  
  Args:
    grouped (Any): Grouped passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _drop_first_bucket_per_host_if_safe(None)  # doctest: +SKIP
  """
  if grouped is None or getattr(grouped, "empty", True):
    return grouped
  if "host" not in grouped.columns:
    return grouped
  keep_idx = []
  for _host, g in grouped.groupby("host", sort=False):
    if len(g) <= 1:
      keep_idx.extend(list(g.index))
    else:
      keep_idx.extend(list(g.index[1:]))
  if not keep_idx:
    return grouped.iloc[0:0]
  return grouped.loc[keep_idx]


class Metrics():
  """
  Computes simple and complex metrics for a list of jobs in parallel and writes.
  
  Attributes:
    _shared_pool: Attribute.
    _shared_pool_kind: Attribute.
    complex_metrics_list: Attribute.
    simple_metrics_list: Attribute.
  """

  def __init__(self) -> None:
    """
    Initialize simple_metrics_list and complex_metrics_list.
    
    Returns:
      None
    
    Examples:
      >>> Metrics()  # doctest: +SKIP
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
            "events": list(LLITE_METADATA_IOPS_EVENTS),
            "conv": 1,
            "units": "iops"
        },
        "avg_sharedfs_bw": {
            "typename": LUSTRE_LLITE_TYPE,
            "events": ["vfs_read_bytes", "vfs_write_bytes"],
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
        "avg_tensor_imma_active": {
            "typename": "nvidia_gpu",
            "events": ["tensor_imma_active"],
            "conv": 0.0,
            "units": "%",
        },
        "avg_tensor_hmma_active": {
            "typename": "nvidia_gpu",
            "events": ["tensor_hmma_active"],
            "conv": 0.0,
            "units": "%",
        },
        "avg_tensor_dfma_active": {
            "typename": "nvidia_gpu",
            "events": ["tensor_dfma_active"],
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
        "avg_flops64b": {
            "typename": "pmc",
            "events": list(INTEL_FP_ARITH_DOUBLE_EVENTS),
            "conv": 1e-9,
            "units": "GF",
        },
        "avg_flops32b": {
            "typename": "pmc",
            "events": list(INTEL_FP_ARITH_SINGLE_EVENTS),
            "conv": 1e-9,
            "units": "GF",
        },
        "avg_arm_int8_ops": {
            "typename": HOST_CPU_HW_TYPE,
            "events": [arm_int8_ops_event_names()[0]],
            "conv": 1e-9,
            "units": "Gops",
        },
        "avg_arm_int16_ops": {
            "typename": HOST_CPU_HW_TYPE,
            "events": [arm_int16_ops_event_names()[0]],
            "conv": 1e-9,
            "units": "Gops",
        },
        "avg_gpu_mem_bw_gbps": {
            "typename": "nvidia_gpu",
            "events": ["gpu_mem_bw_bytes_rate"],
            "conv": 1e-9,
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
                "dram_chan0_bytes",
                "dram_chan1_bytes",
                "dram_chan2_bytes",
                "dram_chan3_bytes",
                "MBW_CHANNEL_0",
                "MBW_CHANNEL_1",
                "MBW_CHANNEL_2",
                "MBW_CHANNEL_3",
                "MBW_CHANNEL_4",
                "MBW_CHANNEL_5",
                "MBW_CHANNEL_6",
                "MBW_CHANNEL_7",
            ],
            "conv": 1 / (1024 * 1024 * 1024),
            "units": "GB/s"
        }
    }

    self.complex_metrics_list = [
        'avg_freq', 'avg_ethbw', 'avg_packetsize',
        'max_fabricbw', 'max_lnetbw', 'max_mds', 'max_packetrate',
        'max_opa_congestion_rate', 'max_numa_remote_rate',
        'max_gpu_power', 'max_node_power_est_w', 'avg_node_power_est_w',
        'job_cpu_gpu_watt_hours',
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

  def __getstate__(self) -> Any:
    """
    Exclude non-picklable runtime pool when sending self to workers.
    
    Returns:
      Any: Open return polymorphism from ``__getstate__``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> __getstate__()  # doctest: +SKIP
    """
    state = dict(self.__dict__)
    state["_shared_pool"] = None
    state["_shared_pool_kind"] = None
    return state

  def __setstate__(self, state: Any) -> None:
    """
    Internal helper to handle setstate.
    
    Args:
      state (Any): State passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> __setstate__(None)  # doctest: +SKIP
    """
    self.__dict__.update(state)
    if "_shared_pool" not in self.__dict__:
      self._shared_pool = None
    if "_shared_pool_kind" not in self.__dict__:
      self._shared_pool_kind = None

  def _worker_process_count(self) -> Any:
    """
    Internal helper to handle worker process count.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._worker_process_count()  # doctest: +SKIP
    """
    return cfg.get_metrics_pool_process_count()

  def _imap_chunksize(self, job_count: int, threads: Any) -> Any:
    """
    Internal helper to handle imap chunksize.
    
    Args:
      job_count (int): Integer value for job count.
      threads (Any): Threads passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._imap_chunksize(0, None)  # doctest: +SKIP
    """
    if job_count <= 0:
      return 1
    # Balance IPC overhead and fairness.
    return max(1, job_count // (threads * 4))

  def ensure_pool(self, pool_kind: str = "metrics-pool") -> Any:
    """
    Create and retain a shared worker pool for repeated run() calls.
    
    Args:
      pool_kind (str): String for pool kind.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics().ensure_pool("x")  # doctest: +SKIP
    """
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

  def close_pool(self) -> None:
    """
    Close retained worker pool (idempotent).
    
    Returns:
      None
    
    Examples:
      >>> Metrics().close_pool()  # doctest: +SKIP
    """
    if self._shared_pool is None:
      return
    _close_pool_bounded(self._shared_pool, METRICS_POOL_JOIN_TIMEOUT_S)
    self._shared_pool = None
    self._shared_pool_kind = None

  def reset_pool_hard(self) -> None:
    """
    Terminate retained worker pool without blocking the scheduler thread.
    
    Detach the pool reference first so ``ensure_pool()`` can create a fresh pool
    while lingering workers are torn down in the background. Blocking on
    ``terminate()``/``join()`` after a wedged worker caused indefinite stalls
    after ``MetricsRunWorkerStallError`` was logged.
    
    Returns:
      None
    
    Examples:
      >>> Metrics().reset_pool_hard()  # doctest: +SKIP
    """
    pool = self._shared_pool
    if pool is None:
      return
    self._shared_pool = None
    self._shared_pool_kind = None

    def _terminate_background() -> None:
      """
      Internal helper to handle terminate background.
      
      Returns:
        None
      
      Examples:
        >>> Metrics()._terminate_background()  # doctest: +SKIP
      """
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
  def run(self, job_list: Any, pool: Any | None = None) -> Any:
    """
    Run metric computation for each job in job_list in a process pool; persist.
    
      results via metrics_data.update_or_create.
    
    Args:
      job_list (Any): Job list passed to this helper.
      pool (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      Exception: Raised when ``run`` hits a ``Exception`` failure path.
      MetricsRunWorkerStallError: Raised when ``run`` hits a
      ``MetricsRunWorkerStallError`` failure path.
    
    Examples:
      >>> Metrics().run(None, None)  # doctest: +SKIP
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

  def job_arc(
    self,
    jt: Any,
    name: Any | None = None,
    typename: Any | None = None,
    events: Any | None = None,
    conv: int = 0,
    units: Any | None = None,
    cache: Any | None = None,
    rows_cache: Any | None = None,
    nonnegative_rate: bool = False,
    host_aggregate: str = "mean",
  ) -> Any:
    """
    Aggregate arc by host and 5m time bucket via Django ORM.
    
    For each sample time: sum ``arc`` across events and devices (instantaneous
    total). Within each 5m bucket: **mean** of those per-time totals (not a sum
    of all rows — summing samples inflated rates by sample count). For each
    host: mean of per-bucket values (after dropping the first bucket). By
    default returns the **arithmetic mean of those per-host values** across
    hosts (most ``avg_*`` simple metrics). When ``host_aggregate="sum"``
    (``avg_cpuusage`` only), returns the **sum** of per-host means
    (job-total busy cores).
    
    When ``nonnegative_rate`` is True, negative ``arc`` samples are dropped
      (NaN)
    before bucketing. Use for cumulative byte counters (fabric bandwidth) where
    a negative rate indicates reset, wrong rollover width, or bad samples.
    
    Args:
      jt (Any): Jt passed to this helper.
      name (Any | None): One of ``Any``, ``None``.
      typename (Any | None): One of ``Any``, ``None``.
      events (Any | None): One of ``Any``, ``None``.
      conv (int): Integer value for conv.
      units (Any | None): One of ``Any``, ``None``.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
      nonnegative_rate (bool): Boolean flag for nonnegative rate.
      host_aggregate (str): String for host aggregate.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_arc(0)  # doctest: +SKIP
    """
    import pandas as pd

    if not getattr(jt, "_base_filter", None):
      return None
    base = jt._base_filter
    hosts = base.get("host__in") or []
    if not hosts:
      return None
    agg = "sum" if host_aggregate == "sum" else "mean"
    cache_key = None
    if cache is not None:
      cache_key = (
          _coerce_metrics_identity_str(typename),
          _hashable_metric_events_signature(events),
          float(conv),
          bool(nonnegative_rate),
          agg,
      )
      if cache_key in cache:
        return cache[cache_key]
    schema = getattr(jt, "schema", None)
    if not _metric_type_events_feasible(schema, typename, events):
      if cache is not None:
        cache[cache_key] = None
      return None
    tkw = _jid_table_host_data_time_kwargs(base)
    if not tkw:
      return None
    rows = []
    for typ in type_probe_names(typename):
      if not _metric_type_events_feasible(schema, typ, events):
        continue
      # Instantaneous total at each sample time (events × devices) is summed in
      # SQL; pulling every device row into pandas burned the per-job wall clock
      # on multi-node PMC jobs (MetricsComputeJobTimeoutError in job_arc).
      rows = _host_data_metric_rows_batched(
          tkw,
          hosts,
          typ,
          events,
          "arc",
          rows_cache=rows_cache,
          sum_per_sample=True,
          nonnegative_only=nonnegative_rate,
      )
      if rows:
        break
    if not rows:
      if cache is not None:
        cache[cache_key] = None
      return None
    per_time = pd.DataFrame(rows)
    if per_time.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    if not pd.api.types.is_datetime64_any_dtype(per_time["time"]):
      per_time["time"] = pd.to_datetime(per_time["time"])
    per_time["bucket"] = per_time["time"].dt.floor("5min")
    # Mean of instantaneous totals within each 5m bucket (not sum of samples).
    grouped = (
        per_time.groupby(["host", "bucket"], as_index=False)["arc"].mean().rename(
            columns={"bucket": "time", "arc": "sum"}
        )
    )
    grouped["sum"] = grouped["sum"] * conv
    if grouped.empty or "host" not in grouped.columns:
      if cache is not None:
        cache[cache_key] = None
      return None
    # Drop first bucket per host when at least one later bucket remains
    # (short jobs with a single bucket must keep that sample).
    grouped = _drop_first_bucket_per_host_if_safe(grouped)
    if grouped.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    per_host_vals = grouped.groupby("host")["sum"].mean()
    if agg == "sum":
      value = float(per_host_vals.sum())
    else:
      value = float(per_host_vals.mean())
    if cache is not None:
      cache[cache_key] = value
    return value

  def job_value_mean(
    self,
    jt: Any,
    typename: Any | None = None,
    events: Any | None = None,
    conv: float = 1.0,
    cache: Any | None = None,
    rows_cache: Any | None = None,
    reject_dcgm_blank: bool = False,
    max_sane: Any | None = None,
  ) -> Any:
    """
    Mean sampled ``value`` by host and 5m bucket (same bucketing as.
    
      ``job_arc``).
    
    ``reject_dcgm_blank`` NaNs out DCGM blank-family gauges before means.
    ``max_sane`` (after ``conv``) rejects impossible magnitudes as missing.
    
    Args:
      jt (Any): Jt passed to this helper.
      typename (Any | None): One of ``Any``, ``None``.
      events (Any | None): One of ``Any``, ``None``.
      conv (float): Floating-point value for conv.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
      reject_dcgm_blank (bool): Boolean flag for reject dcgm blank.
      max_sane (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics().job_value_mean(None, None, None, 0, None, None, True, None)
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
          "vm",
          _coerce_metrics_identity_str(typename),
          _hashable_metric_events_signature(events),
          float(conv),
          bool(reject_dcgm_blank),
          None if max_sane is None else float(max_sane),
      )
      if cache_key in cache:
        return cache[cache_key]
    schema = getattr(jt, "schema", None)
    if not _metric_type_events_feasible(schema, typename, events):
      if cache is not None:
        cache[cache_key] = None
      return None
    tkw = _jid_table_host_data_time_kwargs(base)
    if not tkw:
      return None
    rows = []
    for typ in type_probe_names(typename):
      if not _metric_type_events_feasible(schema, typ, events):
        continue
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
    if reject_dcgm_blank and "value" in df.columns:
      vals = nan_out_dcgm_numeric_blanks(
          df["value"].to_numpy(dtype=np.float64, copy=True))
      df = df.copy()
      df["value"] = vals
      df = df[np.isfinite(df["value"].to_numpy(dtype=np.float64))]
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
    grouped = _drop_first_bucket_per_host_if_safe(grouped)
    if grouped.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    per_host_vals = grouped.groupby("host")["sum"].mean()
    value = float(per_host_vals.mean())
    if max_sane is not None and (
        not np.isfinite(value) or abs(value) > float(max_sane)
    ):
      value = None
    if cache is not None:
      cache[cache_key] = value
    return value

  def _job_avg_cpuusage_allocated(
    self,
    jt: Any,
    job: Any,
    cache: Any | None = None,
    rows_cache: Any | None = None,
  ) -> Any:
    """
    Job-total busy cores scaled to allocated ``ncores`` (not whole-node /proc).
    
    ``host_cpu`` arcs are node-wide (collapsed per-CPU jiffies). Raw sum across
    hosts can far exceed ``ncores`` on shared nodes. Scale each sample by
    ``util = busy / (busy + idle-family)`` times ``ncores / nhosts``, then sum
    per-host means (same bucketing as ``job_arc``).
    
    Args:
      jt (Any): Jt passed to this helper.
      job (Any): Job record (Django ``job_data`` or job-like mapping).
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._job_avg_cpuusage_allocated(None, None, None, None)
    """
    import pandas as pd

    if not getattr(jt, "_base_filter", None):
      return None
    base = jt._base_filter
    hosts = base.get("host__in") or []
    if not hosts:
      return None
    try:
      ncores = float(getattr(job, "ncores", None) or 0)
      nhosts = float(getattr(job, "nhosts", None) or 0)
    except (TypeError, ValueError):
      return None
    if ncores <= 0 or nhosts <= 0:
      return None
    cores_per_host = ncores / nhosts
    busy_events = ["user", "system", "nice"]
    idle_events = ["idle", "iowait", "irq", "softirq"]
    cache_key = None
    if cache is not None:
      cache_key = (
          "avg_cpuusage_alloc",
          float(ncores),
          float(nhosts),
      )
      if cache_key in cache:
        return cache[cache_key]
    tkw = _jid_table_host_data_time_kwargs(base)
    if not tkw:
      return None

    def _sum_arc_events(events: Any) -> Any:
      """
      Internal helper to handle sum arc events.
      
      Args:
        events (Any): Events passed to this helper.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> Metrics()._sum_arc_events(None)  # doctest: +SKIP
      """
      schema = getattr(jt, "schema", None)
      if not _metric_type_events_feasible(schema, HOST_CPU_TYPE, events):
        return None
      rows = []
      for typ in type_probe_names(HOST_CPU_TYPE):
        if not _metric_type_events_feasible(schema, typ, events):
          continue
        rows = _host_data_metric_rows_batched(
            tkw,
            hosts,
            typ,
            events,
            "arc",
            rows_cache=rows_cache,
            sum_per_sample=True,
        )
        if rows:
          break
      if not rows:
        return None
      df = pd.DataFrame(rows)
      if df.empty:
        return None
      if not pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"])
      # Rows are already one per (host, time) from the SQL per-sample sum.
      return df

    busy = _sum_arc_events(busy_events)
    if busy is None or busy.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    busy = busy.rename(columns={"arc": "busy"})
    idle = _sum_arc_events(idle_events)
    if idle is None or idle.empty:
      value = self.job_arc(
          jt,
          typename=HOST_CPU_TYPE,
          events=busy_events,
          conv=0.01,
          units="#cores",
          cache=cache,
          rows_cache=rows_cache,
          host_aggregate="sum",
      )
      if cache is not None:
        cache[cache_key] = value
      return value
    idle = idle.rename(columns={"arc": "idle"})
    merged = busy.merge(idle, on=["host", "time"], how="left")
    merged["idle"] = merged["idle"].fillna(0.0)
    denom = merged["busy"] + merged["idle"]
    util = (merged["busy"] / denom).where(denom > 0, 0.0).clip(0.0, 1.0)
    merged["sum"] = util * cores_per_host
    merged["bucket"] = merged["time"].dt.floor("5min")
    grouped = (
        merged.groupby(["host", "bucket"], as_index=False)["sum"]
        .mean()
        .rename(columns={"bucket": "time"})
    )
    grouped = _drop_first_bucket_per_host_if_safe(grouped)
    if grouped.empty:
      if cache is not None:
        cache[cache_key] = None
      return None
    value = float(grouped.groupby("host")["sum"].mean().sum())
    if cache is not None:
      cache[cache_key] = value
    return value

  def _job_arc_avg_flops_precision(
    self,
    jt: Any,
    events: Any,
    cache: Any | None = None,
    rows_cache: Any | None = None,
    *,
    grace_scalar_events: Any | None = None,
  ) -> Any:
    """
    GFLOP/s from Intel FP_ARITH; else Grace host_cpu_hw scalar events.
    
    Args:
      jt (Any): Jt passed to this helper.
      events (Any): Events passed to this helper.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
      grace_scalar_events (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._job_arc_avg_flops_precision(None, None, None, None, None)
    """
    for core_typ in core_pmc_types_probe_order():
      v = self.job_arc(
          jt,
          typename=core_typ,
          events=list(events),
          conv=1e-9,
          units="GF",
          cache=cache,
          rows_cache=rows_cache,
      )
      if v is not None and float(v) > 0:
        return v, core_typ
    if grace_scalar_events:
      for hw_typ in host_cpu_hw_type_names():
        for flop_ev in grace_scalar_events:
          v = self.job_arc(
              jt,
              typename=hw_typ,
              events=[flop_ev],
              conv=1e-9,
              units="GF",
              cache=cache,
              rows_cache=rows_cache,
          )
          if v is not None and float(v) > 0:
            return v, hw_typ
    return None, None

  def _job_arc_avg_flops(
    self,
    jt: Any,
    cache: Any | None = None,
    rows_cache: Any | None = None,
  ) -> Any:
    """
    GFLOP/s from AMD PMC, else Intel FP_ARITH/SSE, else ARM host_cpu_hw.
    
      estimate.
    
    Args:
      jt (Any): Jt passed to this helper.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._job_arc_avg_flops(None, None, None)  # doctest: +SKIP
    """
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

  def _job_arc_avg_mbw(
    self,
    jt: Any,
    cache: Any | None = None,
    rows_cache: Any | None = None,
  ) -> Any:
    """
    Memory bandwidth (GB/s): AMD DF channels, else Intel/ARM IMC CAS sum.
    
    Args:
      jt (Any): Jt passed to this helper.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._job_arc_avg_mbw(None, None, None)  # doctest: +SKIP
    """
    from hpcperfstats.dbload.lib.monitor_naming.canonical import AMD_DF_STATS_TYPES
    from hpcperfstats.dbload.lib.monitor_naming.resolve import amd_df_bw_event_conv_tries

    for df_typ in amd_df_type_names():
      if df_typ in AMD_DF_STATS_TYPES:
        tries = amd_df_bw_event_conv_tries()[:1]
      else:
        tries = amd_df_bw_event_conv_tries()[::-1]
      for events, conv in tries:
        v = self.job_arc(
            jt,
            typename=df_typ,
            events=list(events),
            conv=conv,
            units="GB/s",
            cache=cache,
            rows_cache=rows_cache,
        )
        if v is not None:
          return v, df_typ
    cas_conv = 64 / (1024 ** 3)
    for imc_typ in imc_types_probe_order():
      dram_v = None
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
          dram_v = v
          break
      hbm_v = None
      for read_ev, write_ev in hbm_cas_read_write_pairs():
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
          hbm_v = v
          break
      combined = combine_cas_bw_scalars(dram_v, hbm_v)
      if combined is not None:
        return combined, imc_typ
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
          events=list(arm_dram_bw_event_names()),
          conv=1 / (1024 ** 3),
          units="GB/s",
          cache=cache,
          rows_cache=rows_cache,
      )
      if v is not None:
        return v, hw_typ
    return None, None

  def _job_arc_avg_sharedfs_iops(
    self,
    jt: Any,
    cache: Any | None = None,
    rows_cache: Any | None = None,
  ) -> Any:
    """
    Shared filesystem IOPS from Lustre llite and NFS operation counters.
    
    Returns summed contribution from available sources and a representative
      type.
    
    Args:
      jt (Any): Jt passed to this helper.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._job_arc_avg_sharedfs_iops(None, None, None)  # doctest: +SKIP
    """
    total = 0.0
    used = []
    llite = self.job_arc(
        jt,
        typename=LUSTRE_LLITE_TYPE,
        events=list(LLITE_METADATA_IOPS_EVENTS),
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

  def _job_arc_avg_sharedfs_bw(
    self,
    jt: Any,
    cache: Any | None = None,
    rows_cache: Any | None = None,
  ) -> Any:
    """
    Shared filesystem bandwidth from Lustre llite and NFS byte counters.
    
    Returns summed contribution from available sources and a representative
      type.
    
    Args:
      jt (Any): Jt passed to this helper.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._job_arc_avg_sharedfs_bw(None, None, None)  # doctest: +SKIP
    """
    conv = 1.0 / (1024 * 1024)
    total = 0.0
    used = []
    llite = self.job_arc(
        jt,
        typename=LUSTRE_LLITE_TYPE,
        events=["vfs_read_bytes", "vfs_write_bytes"],
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

  def _job_arc_avg_ibbw(
    self,
    jt: Any,
    cache: Any | None = None,
    rows_cache: Any | None = None,
  ) -> Any:
    """
    Fabric bandwidth from IB/OPA, with Ethernet fallback when unavailable.
    
    Args:
      jt (Any): Jt passed to this helper.
      cache (Any | None): One of ``Any``, ``None``.
      rows_cache (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics()._job_arc_avg_ibbw(None, None, None)  # doctest: +SKIP
    """
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
        typename=HOST_OPA_TYPE,
        events=["PortXmitData", "PortRcvData"],
        conv=1.0 / 125000,
        units="MB/s",
        cache=cache,
        rows_cache=rows_cache,
        nonnegative_rate=True,
    )
    if v is not None:
      return v, HOST_OPA_TYPE
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
  def compute_metrics(self, job: Any) -> Any:
    """
    Compute metrics for one job; return dict with rows (metrics_data-shaped.
    
      dicts) and distinct_time_count.
    
    distinct_time_count is the sum over hosts of COUNT(DISTINCT time) in
    jid_table._host_data_qs() for this job (not the global distinct time count).
    
    Args:
      job (Any): Job record (Django ``job_data`` or job-like mapping).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> Metrics().compute_metrics(None)  # doctest: +SKIP
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
        from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
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
        if metric_name == "avg_cpuusage":
          value = self._job_avg_cpuusage_allocated(
              jt,
              job,
              cache=simple_metric_cache,
              rows_cache=host_data_rows_cache,
          )
          row_type = metric_obj["typename"]
        elif metric_name == "avg_flops":
          value, flops_typename = self._job_arc_avg_flops(
              jt, cache=simple_metric_cache, rows_cache=host_data_rows_cache)
          row_type = flops_typename or metric_obj["typename"]
        elif metric_name == "avg_flops64b":
          value, flops_typename = self._job_arc_avg_flops_precision(
              jt,
              list(INTEL_FP_ARITH_DOUBLE_EVENTS),
              cache=simple_metric_cache,
              rows_cache=host_data_rows_cache,
              grace_scalar_events=grace_fp_scalar_double_event_names(),
          )
          row_type = flops_typename or metric_obj["typename"]
        elif metric_name == "avg_flops32b":
          value, flops_typename = self._job_arc_avg_flops_precision(
              jt,
              list(INTEL_FP_ARITH_SINGLE_EVENTS),
              cache=simple_metric_cache,
              rows_cache=host_data_rows_cache,
              grace_scalar_events=grace_fp_scalar_single_event_names(),
          )
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
            "avg_tensor_imma_active",
            "avg_tensor_hmma_active",
            "avg_tensor_dfma_active",
            "avg_fp16_active",
            "avg_fp32_active",
            "avg_fp64_active",
        ):
          metric_event = {
              "avg_tensor_active": "tensor_active",
              "avg_tensor_imma_active": "tensor_imma_active",
              "avg_tensor_hmma_active": "tensor_hmma_active",
              "avg_tensor_dfma_active": "tensor_dfma_active",
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
            # Accept mean 0 when samples exist (idle ≠ missing telemetry).
            if v is not None:
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
                reject_dcgm_blank=True,
                max_sane=_MAX_SANE_GPU_LINK_GBPS,
            )
            if v is not None:
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

      from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
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
          from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
              max_node_power_est_w as _max_npe,
          )
          value = _max_npe(jt)
          typename, units = "job", "W"
        elif metric_name == "avg_node_power_est_w":
          from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
              mean_node_power_est_w as _mean_npe,
          )
          value = _mean_npe(jt)
          typename, units = "job", "W"
        elif metric_name == "job_cpu_gpu_watt_hours":
          from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
              job_cpu_gpu_watt_hours as _job_wh,
          )
          value = _job_wh(jt)
          typename, units = "job", "Wh"
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


def job_metrics_catalog_entries() -> Any:
  """
  Ordered catalog of every job-level metric for UI and completeness checks.
  
  Short labels for the Job detail table are defined in
  ``hpcperfstats.analysis.metrics.lib.job_metric_display_labels.JOB_METRIC_SHORT
    _LABELS``
  (Python) and mirrored in the SPA
  ``hpcperfstats/site/frontend/src/utils/jobMetricDisplayLabels.js``.
  
  Returns:
    Any: Open return polymorphism from ``job_metrics_catalog_entries``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Raises:
    RuntimeError: Raised when ``job_metrics_catalog_entries`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> job_metrics_catalog_entries()  # doctest: +SKIP
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


def expected_job_metric_row_count() -> Any:
  """
  Expected job metric row count.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> expected_job_metric_row_count()  # doctest: +SKIP
  """
  return len(job_metrics_catalog_entries())


def build_job_metrics_display_list(job: Any) -> Any:
  """
  API: full metrics_list with a row per catalog metric (value or.
  
    no_data_reason).
  
  Args:
    job (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_job_metrics_display_list(None)  # doctest: +SKIP
  """
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
  # Job detail UI tiers: valued metrics, then error/Insufficient, then not-computed last.
  def _display_tier(row: Any) -> Any:
    """
    Internal helper to handle display tier.
    
    Args:
      row (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _display_tier(None)  # doctest: +SKIP
    """
    if row.get("value") is not None:
      return 0
    if row.get("no_data_reason") == METRIC_NOT_COMPUTED_YET:
      return 2
    return 1

  out.sort(key=_display_tier)
  # Hide duplicate avg_gpuutil when it equals detail_gpu_util_mean (same persist path).
  mean_row = next(
      (r for r in out if r.get("metric") == "detail_gpu_util_mean"),
      None,
  )
  if mean_row is not None and mean_row.get("value") is not None:
    try:
      mean_v = float(mean_row["value"])
    except (TypeError, ValueError):
      mean_v = None
    if mean_v is not None:
      filtered = []
      for r in out:
        if r.get("metric") != "avg_gpuutil" or r.get("value") is None:
          filtered.append(r)
          continue
        try:
          if abs(float(r["value"]) - mean_v) < 1e-9:
            continue
        except (TypeError, ValueError):
          pass
        filtered.append(r)
      out = filtered
  return out


def _gate_failure_catalog_already_clean(jid: Any) -> Any:
  """
  True when jid already has a full insufficient gate-failure catalog and no.
  
    artifacts.
  
  Args:
    jid (Any): Jid passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _gate_failure_catalog_already_clean(None)  # doctest: +SKIP
  """
  expected = expected_job_metric_row_count()
  qs = metrics_data.objects.filter(jid_id=jid)
  if qs.count() != expected:
    return False
  if qs.filter(value__isnull=False).exists():
    return False
  if qs.exclude(no_data_reason=INSUFFICIENT_DATA_FOR_METRICS_PROCESSING).exists():
    return False
  from hpcperfstats.site.lib.machine.models import job_detail_artifact, job_plot_artifact

  if job_plot_artifact.objects.filter(jid_id=jid).exists():
    return False
  if job_detail_artifact.objects.filter(jid_id=jid).exists():
    return False
  return True


def persist_window_coverage_gate_failure(
  jid: Any,
  *,
  telemetry_first_time: Any | None = None,
  telemetry_last_time: Any | None = None,
  distinct_time_count: Any | None = None,
) -> Any:
  """
  Remove stale metrics/plots and persist full catalog with gate-failure reason.
  
  Returns True when a write occurred; False when idempotent skip or invalid jid.
  
  Args:
    jid (Any): Jid passed to this helper.
    telemetry_first_time (Any | None): One of ``Any``, ``None``.
    telemetry_last_time (Any | None): One of ``Any``, ``None``.
    distinct_time_count (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> persist_window_coverage_gate_failure(None, None, None, None)
  """
  jid = str(jid or "").strip()
  if not jid:
    return False
  if _gate_failure_catalog_already_clean(jid):
    return False

  from hpcperfstats.site.lib.machine.cache_utils import (
      invalidate_jid_derived_cache_keys,
      invalidate_job_plot_cache_keys_for_jids,
  )
  from hpcperfstats.site.lib.machine.job_plot_artifacts import (
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
  """
  Average CPU frequency (GHz) from PMC.
  
  Uses CLOCKS_UNHALTED_CORE/CLOCKS_UNHALTED_REF when present; otherwise
    APERF/MPERF
  with the same nominal reference scaling as Intel (u.freq * APERF/MPERF).
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> avg_freq().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  Average Ethernet bandwidth (MB/s) from net rx_bytes/tx_bytes.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> avg_ethbw().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  Average GPU utilization (%) from nvidia_gpu or amd_gpu.
  """

  def _avg_gpuutil_for_event(
    self,
    u: Any,
    typename: Any,
    event_name: Any,
  ) -> Any:
    """
    Mean utilization (%) for one ``typename`` / ``event_name``, or None if.
    
      unusable.
    
    Args:
      u (Any): U passed to this helper.
      typename (Any): Typename passed to this helper.
      event_name (Any): Event name passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> avg_gpuutil()._avg_gpuutil_for_event(None, None, None)  # doctest: +SKIP
    """
    schema, _stats = u.get_type(typename)
    if schema is None or event_name not in schema.events:
      return None
    ui = schema[event_name].index
    per_host = []
    for hostname, stats in _stats.items():
      window = nan_out_dcgm_numeric_blanks(stats[1:-1, ui])
      finite = window[np.isfinite(window)]
      if finite.size == 0:
        continue
      per_host.append(float(mean(finite)))
    if not per_host:
      return None
    value = float(mean(per_host))
    if value == 0:
      return None
    return value, typename, '%'

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> avg_gpuutil().compute_metric(None)  # doctest: +SKIP
    """
    for typename, events in (
        ("nvidia_gpu", ("gpu_util", "utilization")),
        ("amd_gpu", ("gpu_util",)),
        ("intel_gpu", ("gpu_util", "utilization")),
    ):
      for event_name in events:
        r = self._avg_gpuutil_for_event(u, typename, event_name)
        if r is not None:
          return r
    return None, "gpu", '%'


class avg_packetsize():
  """
  Average packet size (MB) from host_ib or opa port xmit/rcv data and packets.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> avg_packetsize().compute_metric(None)  # doctest: +SKIP
    """
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
      opa_schema, opa_stats, opa_typename = resolve_get_type(u, (HOST_OPA_TYPE,))
      if opa_schema is not None and _schema_has_events(
          opa_schema,
          "PortXmitPkts",
          "PortRcvPkts",
          "PortXmitData",
          "PortRcvData",
      ):
        typename = opa_typename or HOST_OPA_TYPE
        schema, _stats = opa_schema, opa_stats
        tx, rx = schema["PortXmitPkts"].index, schema["PortRcvPkts"].index
        tb, rb = schema["PortXmitData"].index, schema["PortRcvData"].index
        conv2mb = 125000
      else:
        net_schema, net_stats, net_typename = resolve_get_type(u, ("net",))
        if net_schema is None or not _schema_has_events(
            net_schema,
            "tx_packets",
            "rx_packets",
            "tx_bytes",
            "rx_bytes",
        ):
          return None, HOST_IB_TYPE, 'MB'
        typename = net_typename or "net"
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


# Fabric peak above this is treated as bad telemetry (bytes/s mislabeled as MB/s).
_MAX_FABRIC_BW_SANITY_MB_S = 1_000_000.0


class max_fabricbw():
  """
  Maximum fabric bandwidth (MB/s) from host_ib or host_opa port data.
  
  Uses the same MiB / OPA flit conversions as ``Metrics._job_arc_avg_ibbw``.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_fabricbw().compute_metric(None)  # doctest: +SKIP
    """
    max_bw = 0
    schema, _stats, typename = resolve_get_type(u, (HOST_IB_TYPE,))
    if schema is not None and _schema_has_events(
        schema, "port_xmit_data", "port_rcv_data"):
      tx, rx = schema["port_xmit_data"].index, schema["port_rcv_data"].index
      conv2mb = 1024 * 1024
    else:
      schema, _stats, typename = resolve_get_type(u, (HOST_OPA_TYPE,))
      if schema is not None and _schema_has_events(
          schema, "PortXmitData", "PortRcvData"):
        tx, rx = schema["PortXmitData"].index, schema["PortRcvData"].index
        conv2mb = 125000
      else:
        schema, _stats, typename = resolve_get_type(u, ("net",))
        if schema is None or not _schema_has_events(
            schema, "tx_bytes", "rx_bytes"):
          return None, HOST_IB_TYPE, 'MB/s'
        tx, rx = schema["tx_bytes"].index, schema["rx_bytes"].index
        conv2mb = 1024 * 1024
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [tx, rx], conv2mb)
    if cluster_peak is not None:
      if cluster_peak > _MAX_FABRIC_BW_SANITY_MB_S:
        return None, typename, 'MB/s'
      return cluster_peak, typename, 'MB/s'
    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(_add_arrays(stats[:, tx], stats[:, rx]), u.t)
      fin = ratio[np.isfinite(ratio)]
      if fin.size > 0:
        max_bw = max(max_bw, fin.max())
    if max_bw == 0:
      return None, typename, 'MB/s'
    value = max_bw / conv2mb
    if value > _MAX_FABRIC_BW_SANITY_MB_S:
      return None, typename, 'MB/s'
    return value, typename, 'MB/s'


class max_lnetbw():
  """
  Maximum LNET bandwidth (MB/s) from lnet tx_bytes/rx_bytes.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_lnetbw().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  Maximum Lustre MDS operations (iops) from llite vfs_*_ops (dual-read legacy).
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_mds().compute_metric(None)  # doctest: +SKIP
    """
    max_mds = 0
    typename = LUSTRE_LLITE_TYPE
    schema, _stats, resolved_typ = resolve_get_type(u, type_probe_names(typename))
    mds_cols = list(LLITE_METADATA_IOPS_EVENTS)
    if schema is not None and _schema_has_events_for_type(
        schema, resolved_typ or typename, *mds_cols
    ):
      col_idx = [
          _schema_event_index(schema, resolved_typ or typename, c) for c in mds_cols
      ]
      cluster_peak = _peak_interval_rate_from_cluster_mean(
          u, resolved_typ or typename, col_idx, 1)
      if cluster_peak is not None:
        return cluster_peak, typename, 'iops'
      for hostname, stats in _stats.items():
        mds_sum = None
        for idx in col_idx:
          col = stats[:, idx]
          mds_sum = col if mds_sum is None else mds_sum + col
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
      return None, typename, 'iops'
    value = max_mds
    return value, typename, 'iops'


class max_packetrate():
  """
  Maximum packet rate (#/s) from host_ib or opa port xmit/rcv packets.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_packetrate().compute_metric(None)  # doctest: +SKIP
    """
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
        u, typename, [tx, rx], 1, max_sane=_MAX_SANE_PACKETRATE)
    if cluster_peak is not None:
      return cluster_peak, typename, '#/s'

    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(_add_arrays(stats[:, tx], stats[:, rx]), u.t)
      peak = _sane_peak_from_rates(
          ratio, divisor=1.0, max_sane=_MAX_SANE_PACKETRATE)
      if peak is not None:
        max_pr = max(max_pr, peak)
    if max_pr == 0:
      return None, typename, '#/s'
    value = max_pr
    return value, typename, '#/s'


# This will compute the maximum memory usage recorded
# by monitor.  It only samples at x mn intervals and
# may miss high water marks in between.
class mem_hwm():
  """
  Memory high-water mark (GiB) from host_mem/mem used − slab − file pages.

  Monitor emits KB (``mem_used`` / ``slab`` / ``file_pages``); dual-read also
  accepts legacy PascalCase event names. Peak KB is scaled by ``1024**2``
  (Summary-aligned), not treated as bytes.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Peak (MemUsed − Slab − FilePages) over hosts, in GiB.
    
    Args:
      u (Any): Job utils view with ``get_type`` for ``host_mem`` / ``mem``.
    
    Returns:
      Any: ``(value_or_None, HOST_MEM_TYPE, 'GiB')``.
    
    Examples:
      >>> mem_hwm().compute_metric(None)  # doctest: +SKIP
    """
    max_memusage = 0.0
    typename = HOST_MEM_TYPE
    schema, _stats, resolved_typ = resolve_get_type(
        u, type_probe_names(typename))
    typ = resolved_typ or typename
    if schema is None or not _schema_has_events_for_type(
        schema, typ, "MemUsed", "Slab", "FilePages"):
      return None, typename, 'GiB'
    mem_i = _schema_event_index(schema, typ, "MemUsed")
    slab_i = _schema_event_index(schema, typ, "Slab")
    file_i = _schema_event_index(schema, typ, "FilePages")
    for hostname, stats in _stats.items():
      mem_arr = (
          stats[:, mem_i] - stats[:, slab_i] - stats[:, file_i])
      peak = _finite_amax(mem_arr)
      if peak is not None:
        max_memusage = max(max_memusage, peak)
    if max_memusage == 0:
      return None, typename, 'GiB'
    # Monitor host_mem values are KB (see Summary mem_used scale).
    value = max_memusage / (1024.0 ** 2)
    return value, typename, 'GiB'


def _flops_weighted_events_for_schema(schema: Any) -> Any:
  """
  Return [(event, weight), ...] for total FLOP-equivalent arc columns, or None.
  
  Args:
    schema (Any): Schema passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _flops_weighted_events_for_schema(None)  # doctest: +SKIP
  """
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


def _node_imbalance_percent_weighted(
  u: Any,
  typename: Any,
  weighted_events: Any,
) -> Any:
  """
  Like ``node_imbalance`` but on a weighted sum of counter columns.
  
  Args:
    u (Any): U passed to this helper.
    typename (Any): Typename passed to this helper.
    weighted_events (Any): Weighted events passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _node_imbalance_percent_weighted(None, None, None)  # doctest: +SKIP
  """
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
  """
  Peak interval rate of summed OPA congestion-related counters (events/s).
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_opa_congestion_rate().compute_metric(None)  # doctest: +SKIP
    """
    typename = HOST_OPA_TYPE
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
  """
  Peak interval rate of NUMA remote-access counters (miss/foreign/other_node).
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_numa_remote_rate().compute_metric(None)  # doctest: +SKIP
    """
    typename = HOST_NUMA_TYPE
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
  """
  FLOPs rate imbalance across nodes (%), same construction as.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> flops_node_imbalance().compute_metric(None)  # doctest: +SKIP
    """
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


def _dram_bw_weighted_events_for_imbalance(u: Any) -> Any:
  """
  Return (typename, [(event, weight), ...]) for DRAM CAS/MBW imbalance, or.
  
    (None, None).
  
  Args:
    u (Any): U passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _dram_bw_weighted_events_for_imbalance(None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.monitor_naming.canonical import DRAM_CHAN_BYTES_EVENTS
  from hpcperfstats.dbload.lib.monitor_naming.legacy import LEGACY_AMD_DF_MBW_CHANNEL_EVENTS

  for df_typ in amd_df_type_names():
    schema_df, _, _ = resolve_get_type(u, (df_typ,))
    if schema_df is not None:
      for chans in (DRAM_CHAN_BYTES_EVENTS, LEGACY_AMD_DF_MBW_CHANNEL_EVENTS):
        found = [c for c in chans if c in schema_df]
        if found:
          return df_typ, [(c, 1.0) for c in found]
  imc = u.imc
  if not imc:
    return None, None
  schema_imc, _, imc_typ = resolve_get_type(u, (imc,))
  if schema_imc is None:
    return None, None
  weighted = []
  for read_ev, write_ev in dram_cas_read_write_pairs():
    pair = []
    if read_ev in schema_imc:
      pair.append((read_ev, 1.0))
    if write_ev in schema_imc:
      pair.append((write_ev, 1.0))
    if pair:
      weighted.extend(pair)
      break
  for read_ev, write_ev in hbm_cas_read_write_pairs():
    pair = []
    if read_ev in schema_imc:
      pair.append((read_ev, 1.0))
    if write_ev in schema_imc:
      pair.append((write_ev, 1.0))
    if pair:
      weighted.extend(pair)
      break
  if weighted:
    return imc_typ, weighted
  return None, None


def _node_imbalance_instantaneous_percent(
  u: Any,
  typename: Any,
  event_name: Any,
) -> Any:
  """
  Imbalance for snapshot ``value`` columns (e.g. GPU util): per-time max vs.
  
    each.
  
    host.
  
  Args:
    u (Any): U passed to this helper.
    typename (Any): Typename passed to this helper.
    event_name (Any): Event name passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _node_imbalance_instantaneous_percent(None, None, None)
  """
  schema, _stats = u.get_type(typename)
  if schema is None or event_name not in schema or not _stats:
    return None
  j = schema[event_name].index
  nt = u.nt
  max_per_t = np.full(nt, -np.inf, dtype=np.float64)
  for hostname, stats in _stats.items():
    col = nan_out_dcgm_numeric_blanks(stats[:, j].astype(np.float64))
    max_per_t = np.maximum(max_per_t, np.nan_to_num(col, nan=-np.inf))
  max_imbalance = []
  for hostname, stats in _stats.items():
    v = nan_out_dcgm_numeric_blanks(stats[:, j].astype(np.float64))
    valid = (max_per_t > 0) & np.isfinite(v)
    if not np.any(valid):
      max_imbalance.append(float("nan"))
      continue
    rel = (max_per_t[valid] - v[valid]) / max_per_t[valid]
    max_imbalance.append(float(mean(rel)))
  if not max_imbalance:
    return None
  return 100 * amax([0. if isnan(x) else x for x in max_imbalance])


class max_gpu_power():
  """
  Peak GPU power draw (W) from ``nvidia_gpu`` / ``amd_gpu`` / ``intel_gpu``.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_gpu_power().compute_metric(None)  # doctest: +SKIP
    """
    for typename in ("nvidia_gpu", "amd_gpu", "intel_gpu"):
      schema, _stats = u.get_type(typename)
      if schema is None or "power_usage" not in schema or not _stats:
        continue
      j = schema["power_usage"].index
      mx = 0.0
      used = False
      for hostname, stats in _stats.items():
        col = stats[:, j].astype(float)
        peak = _finite_amax(col, reject_dcgm_blank=True)
        if peak is not None:
          mx = max(mx, peak)
          used = True
      if used and mx > 0:
        return mx, typename, "W"
    return None, "nvidia_gpu", "W"


class max_gpu_link_gbps():
  """
  Peak PCIe+NVLink byte rate (GB/s) from ``nvidia_gpu``.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_gpu_link_gbps().compute_metric(None)  # doctest: +SKIP
    """
    typename = "nvidia_gpu"
    schema, _stats = u.get_type(typename)
    if schema is None or "gpu_io_link_total_bytes" not in schema:
      return None, typename, "GB/s"
    j = schema["gpu_io_link_total_bytes"].index
    cluster_peak = _peak_interval_rate_from_cluster_mean(
        u, typename, [j], 1e9, max_sane=_MAX_SANE_GPU_LINK_GBPS)
    if cluster_peak is not None:
      return cluster_peak, typename, "GB/s"
    max_bw = 0.0
    for hostname, stats in _stats.items():
      ratio = _per_interval_rate(stats[:, j], u.t)
      peak = _sane_peak_from_rates(
          ratio, divisor=1e9, max_sane=_MAX_SANE_GPU_LINK_GBPS)
      if peak is not None:
        max_bw = max(max_bw, peak)
    if max_bw <= 0:
      return None, typename, "GB/s"
    return max_bw, typename, "GB/s"


class max_gpu_clock_event_reasons():
  """
  Maximum observed DCGM clock throttle reason bitmask (opaque; non-zero implies.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> max_gpu_clock_event_reasons().compute_metric(None)  # doctest: +SKIP
    """
    mx = 0
    used = None
    for typename in ("nvidia_gpu", "amd_gpu", "intel_gpu"):
      schema, _stats = u.get_type(typename)
      if schema is None or "clocks_event_reasons" not in schema or not _stats:
        continue
      j = schema["clocks_event_reasons"].index
      vendor_hit = False
      for hostname, stats in _stats.items():
        col = stats[:, j].astype(np.float64)
        peak = _finite_amax(col, reject_dcgm_blank=True)
        if peak is None or is_dcgm_numeric_blank(peak):
          continue
        cmax = int(peak)
        if cmax > mx:
          mx = cmax
          used = typename
          vendor_hit = True
      if vendor_hit:
        break  # first vendor with usable samples wins
    if used is None or mx == 0:
      return None, "nvidia_gpu", "#"
    return float(mx), used, "#"


class dram_bw_node_imbalance():
  """
  DRAM bandwidth rate imbalance across nodes (%); AMD DF MBW or Intel IMC CAS.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> dram_bw_node_imbalance().compute_metric(None)  # doctest: +SKIP
    """
    typename, we = _dram_bw_weighted_events_for_imbalance(u)
    if not typename or not we:
      return None, "imc", "%"
    v = _node_imbalance_percent_weighted(u, typename, we)
    if v is None:
      return None, typename, "%"
    return v, typename, "%"


class lnet_node_imbalance():
  """
  LNET tx+rx byte rate imbalance across nodes (%).
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> lnet_node_imbalance().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  GPU utilization imbalance across nodes from snapshot ``gpu_util`` (or legacy.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> gpu_util_node_imbalance().compute_metric(None)  # doctest: +SKIP
    """
    for typename, events in (
        ("nvidia_gpu", ("gpu_util", "utilization")),
        ("amd_gpu", ("gpu_util",)),
        ("intel_gpu", ("gpu_util", "utilization")),
    ):
      for ev in events:
        v = _node_imbalance_instantaneous_percent(u, typename, ev)
        if v is not None:
          return v, typename, "%"
    return None, "nvidia_gpu", "%"


class tensor_node_imbalance():
  """
  Tensor-pipe activity imbalance across nodes (``tensor_active`` snapshot).
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> tensor_node_imbalance().compute_metric(None)  # doctest: +SKIP
    """
    for typename in ("nvidia_gpu", "amd_gpu", "intel_gpu"):
      v = _node_imbalance_instantaneous_percent(u, typename, "tensor_active")
      if v is not None:
        return v, typename, "%"
    return None, "nvidia_gpu", "%"


class fabric_node_imbalance():
  """
  Fabric byte-rate imbalance across nodes (%); prefers ``host_ib`` then ``opa``.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> fabric_node_imbalance().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  CPU node imbalance (%): max deviation of per-node CPU rate from max rate.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> node_imbalance().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  CPU time imbalance (%): minimum ratio of integral after/before a time slice.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> time_imbalance().compute_metric(None)  # doctest: +SKIP
    """
    typename = "cpu"
    schema, _stats = u.get_type(typename)
    if schema is None or "user" not in schema:
      return None, typename, '%'
    tmid = (u.t[:-1] + u.t[1:]) / 2.0
    user_i = schema["user"].index
    vals = []
    for hostname, stats in _stats.items():
      rate = _per_interval_rate(stats[:, user_i], u.t)
      rate = np.nan_to_num(rate, nan=0.0, posinf=0.0, neginf=0.0)
      # Cumulative CPU jiffies are monotonic; negative dy/dt is reset/wrap/noise.
      rate = np.maximum(rate, 0.0)
      host_min = _time_imbalance_min_ratio_for_rate(rate, tmid)
      if host_min is not None:
        vals.append(host_min)
    if vals:
      value = 100 * min(vals)
      return value, typename, '%'
    else:
      return None, typename, '%'


class vecpercent_64b():
  """
  Percentage of 64b vectorized FLOPs vs total (from PMC events).
  
  Requires Intel-style FP_ARITH double events and/or legacy SSE/AVX double
  counter names. AMD ``amd64_pmc`` typically exposes only aggregate ``FLOPS``,
  so this metric usually has no data on AMD until width-resolved events exist.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> vecpercent_64b().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  Average 64b vector width (FLOPs-weighted) from PMC events.
  
  Same event requirements as ``vecpercent_64b``; not populated from aggregate
  AMD ``FLOPS`` alone.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> avg_vector_width_64b().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  Percentage of 32b vectorized FLOPs vs total (from PMC events).
  
  Uses Intel FP_ARITH single-precision events only; no AMD aggregate FLOPS path.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> vecpercent_32b().compute_metric(None)  # doctest: +SKIP
    """
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
  """
  Average 32b vector width (FLOPs-weighted) from PMC events.
  
  Same as ``vecpercent_32b``: Intel FP_ARITH single events; not AMD FLOPS-wide.
  """

  def compute_metric(self, u: Any) -> Any:
    """
    Compute the metric.
    
    Args:
      u (Any): U passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> avg_vector_width_32b().compute_metric(None)  # doctest: +SKIP
    """
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
