"""
Pure parsing helpers for stats files (no Django). Used by sync_timedb and by
unit tests.

Attributes:
  EVENTMAPS_BY_TYPE: Attribute.
  HOST_PROC_KEYS: Attribute.
  HOST_PROC_PEAK_KEYS: Attribute.
  OnlineMergedProcRows: Marker list for already peak-merged host_proc rows.
  STREAM_PARSE_LINE_BATCH: Attribute.
  _HOST_PROC_KEY_SET: Attribute.
  PARSE_STAGE_BUILD_DF_PARTS: Attribute.
  PARSE_STAGE_COLLAPSE_PARTS: Collapse sub-hold keys summed into derived collapse_s.
  PARSE_STAGE_HOLD_KEYS: Attribute.
  PARSE_STAGE_LOG_KEYS: Attribute.
  _parse_stage_campaign: Process-wide closed-book stage totals.
  _parse_stage_campaign_lock: Mutex for campaign totals (flush only).
  _parse_stage_flushed_s: Per-file campaign-flush watermark ContextVar.
  _parse_stage_hold_stack: Nested exclusive hold stack ContextVar.
  _parse_stage_s: Per-file stage ContextVar.
  _parse_stage_telem_on: Process-wide parse-stage timing enable flag.
  _STATS_COL_NAMES: Attribute.
  _ARC_GROUP_COLS: Attribute.
  _COLLAPSE_GROUP_COLS: Attribute.
  _COLLAPSE_GROUP_COLS_WITH_DEV: Attribute.
  _COUNTER_GROUP_COLS: Attribute.
  _DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS: Attribute.
  _EMPTY_DELTA_ARC_COLUMNS: Attribute.
  _GPU_STATS_TYPES: Attribute.
  _HOST_CPU_HW_TYPES: Attribute.
  _HOST_MEM_JOB_TYPE: Attribute.
  _NVIDIA_GPU_KNOWN_EVENTS: Attribute.
  _NVIDIA_GPU_MAX_EVENTS: Attribute.
  _NVIDIA_GPU_MEAN_EVENTS: Attribute.
  _NVIDIA_GPU_OR_EVENTS: Attribute.
  _NVIDIA_GPU_SUM_EVENTS: Attribute.
  _NVIDIA_EVENT_TO_CLASS: Event name -> collapse class code for single-pass partition.
  _NVIDIA_EVENT_CLASS_SUM: Attribute.
  _NVIDIA_EVENT_CLASS_MAX: Attribute.
  _NVIDIA_EVENT_CLASS_MEAN: Attribute.
  _NVIDIA_EVENT_CLASS_OR: Attribute.
  _NVIDIA_GROUP_KEY_EVENT_INDEX: Attribute.
  _READ_LOOP_DEADLINE_EVERY_BYTES: Attribute.
  _READ_LOOP_DEADLINE_EVERY_LINES: Attribute.
  _SLOW_TIER_OPT: Attribute.
  _TIER_MARKERS: Attribute.
  exclude_types: Attribute.
  map_hardware_counter_vals: Attribute.
"""

from __future__ import annotations

import contextvars
import ctypes
import os
import threading
import time
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame, concat

from hpcperfstats.dbload.lib import (
  sync_timedb_parsing_legacy as legacy_parsing,
  sync_timedb_wait_telemetry as wait_telem,
)
from hpcperfstats.dbload.lib.file_locking import (
  LOCK_SUFFIX,
  file_read_lock_wait,
)
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  DCGM_CPU_POWER_LIMIT_W,
  DCGM_CPU_POWER_UTIL_W,
  HOST_CPU_HW_TYPE,
)
from hpcperfstats.dbload.lib.monitor_naming.legacy import (
  LEGACY_HOST_CPU_HW_TYPE,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  schema_needs_legacy_hardware_decode,
)
from hpcperfstats.lib.dcgm_blank import (
  DCGM_FP64_BLANK,
  is_dcgm_numeric_blank,
  nan_out_dcgm_numeric_blanks,
)

# Types skipped on ingest (canonical monitor names).
exclude_types = frozenset(
  {
    "intel_x86_uncore_cha_skx",
    "host_ps",
    "host_sysv_shm",
    "host_tmpfs",
    "host_vfs",
    # Legacy archives may still use old schema labels.
    "ib",
    "ib_sw",
    "intel_skx_cha",
    "ps",
    "sysv_shm",
    "tmpfs",
    "vfs",
  }
)

# Default host_proc KEYS matching monitor/src/proc.c. Proc-field ingest is a
# T0 smoke contract (docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md), not a stall fix.
HOST_PROC_KEYS = (
  "uid",
  "vm_peak",
  "vm_size",
  "vm_lck",
  "vm_hwm",
  "vm_rss",
  "vm_data",
  "vm_stk",
  "vm_exe",
  "vm_lib",
  "vm_pte",
  "vm_swap",
  "threads",
)
_HOST_PROC_KEY_SET = frozenset(HOST_PROC_KEYS)

# Exhaustive parse-stage telemetry (INI-gated; default off).
# Hold keys are timed; build_df_s / collapse_s / stages_sum_s /
# parse_unaccounted_s are derived (collapse_s = sum of COLLAPSE_PARTS).
PARSE_STAGE_COLLAPSE_PARTS: tuple[str, ...] = (
  "collapse_normalize_s",
  "collapse_host_sum_s",
  "collapse_ccm_s",
  "collapse_gpu_s",
  "collapse_concat_s",
  "collapse_sort_s",
)
PARSE_STAGE_HOLD_KEYS: tuple[str, ...] = (
  "lock_s",
  "decode_s",
  "feed_s",
  "proc_merge_s",
  "hw_df_s",
  "proc_df_s",
  "delta_s",
  *PARSE_STAGE_COLLAPSE_PARTS,
  "arc_s",
  "concat_s",
  "start_s",
  "chunk_setup_s",
  "take_cols_s",
  "heap_release_s",
  "jid_invalidate_s",
)
PARSE_STAGE_BUILD_DF_PARTS: tuple[str, ...] = (
  "proc_merge_s",
  "hw_df_s",
  "proc_df_s",
)
PARSE_STAGE_LOG_KEYS: tuple[str, ...] = (
  *PARSE_STAGE_HOLD_KEYS,
  "build_df_s",
  "collapse_s",
  "stages_sum_s",
  "parse_unaccounted_s",
)
_parse_stage_telem_on = False
_parse_stage_campaign: dict[str, float] = dict.fromkeys(
  PARSE_STAGE_HOLD_KEYS, 0.0
)
_parse_stage_campaign_lock = threading.Lock()
_parse_stage_s: contextvars.ContextVar[dict[str, float] | None] = (
  contextvars.ContextVar(
    "parse_stage_s",
    default=None,
  )
)
_parse_stage_flushed_s: contextvars.ContextVar[dict[str, float] | None] = (
  contextvars.ContextVar(
    "parse_stage_flushed_s",
    default=None,
  )
)
_parse_stage_hold_stack: contextvars.ContextVar[
  list[tuple[str, float | None, float]] | None
] = contextvars.ContextVar(
  "parse_stage_hold_stack",
  default=None,
)


def _parse_stage_acc() -> dict[str, float]:
  """
  Return per-context stage accumulator, lazily initialized when telemetry is on.

  Returns:
    dict[str, float]: Mutable hold-key seconds map for the current context.

  Examples:
    >>> isinstance(_parse_stage_acc(), dict)
    True
  """
  cur = _parse_stage_s.get()
  if cur is None:
    cur = dict.fromkeys(PARSE_STAGE_HOLD_KEYS, 0.0)
    _parse_stage_s.set(cur)
  return cur


def _parse_stage_flushed_acc() -> dict[str, float]:
  """
  Return per-context watermark of stage seconds already flushed to campaign.

  Returns:
    dict[str, float]: Mutable hold-key seconds already added to campaign.

  Examples:
    >>> isinstance(_parse_stage_flushed_acc(), dict)
    True
  """
  cur = _parse_stage_flushed_s.get()
  if cur is None:
    cur = dict.fromkeys(PARSE_STAGE_HOLD_KEYS, 0.0)
    _parse_stage_flushed_s.set(cur)
  return cur


def _parse_stage_derived(acc: dict[str, float]) -> dict[str, float]:
  """
  Build hold keys plus derived ``build_df_s`` / ``stages_sum_s`` from holds.

  Args:
    acc (dict[str, float]): Hold-key seconds (missing treated as 0).

  Returns:
    dict[str, float]: Every hold key plus derived sums.

  Examples:
    >>> _parse_stage_derived({"feed_s": 1.0})["stages_sum_s"] >= 1.0
    True
  """
  out = {key: float(acc.get(key, 0.0)) for key in PARSE_STAGE_HOLD_KEYS}
  out["build_df_s"] = sum(out[key] for key in PARSE_STAGE_BUILD_DF_PARTS)
  out["collapse_s"] = sum(out[key] for key in PARSE_STAGE_COLLAPSE_PARTS)
  out["stages_sum_s"] = sum(out[key] for key in PARSE_STAGE_HOLD_KEYS)
  return out


def reset_parse_stage_timing(*, enabled: bool | None = None) -> None:
  """
  Zero per-file parse-stage ContextVar; optionally set process-wide enable.

  Process-wide enable is sticky across per-file ``enabled=None`` resets so
  ingest pool workers keep recording after a controller enable. Explicit
  ``enabled=True`` also zeros the campaign totals used by closed-book E2.

  Args:
    enabled (bool | None): When ``None``, keep process-wide enable if already
      on; otherwise prefer test env
      ``HPCPERFSTATS_SYNC_INGEST_TELEMETRY``, else read
      ``sync_ingest_telemetry`` from conf. When ``False``, no
      ``perf_counter`` holds run.

  Returns:
    None

  Examples:
    >>> reset_parse_stage_timing(enabled=False)
  """
  global _parse_stage_telem_on
  # Preserve prior file seconds in campaign before clearing ContextVars when
  # sticky-on ``enabled=None`` (per-file reset). Explicit enable/disable zeros
  # campaign under the lock below, so a flush would be discarded.
  if _parse_stage_telem_on and enabled is None:
    _flush_parse_stage_campaign_from_file_acc()
  if enabled is None:
    if not _parse_stage_telem_on:
      from hpcperfstats.dbload.lib import conf_parser as cfg

      with _parse_stage_campaign_lock:
        if cfg.ingest_telemetry_enabled_from_env():
          _parse_stage_telem_on = True
        else:
          _parse_stage_telem_on = bool(cfg.get_sync_ingest_telemetry())
        if _parse_stage_telem_on:
          for key in PARSE_STAGE_HOLD_KEYS:
            _parse_stage_campaign[key] = 0.0
  else:
    with _parse_stage_campaign_lock:
      _parse_stage_telem_on = bool(enabled)
      for key in PARSE_STAGE_HOLD_KEYS:
        _parse_stage_campaign[key] = 0.0
  _parse_stage_s.set(dict.fromkeys(PARSE_STAGE_HOLD_KEYS, 0.0))
  _parse_stage_flushed_s.set(dict.fromkeys(PARSE_STAGE_HOLD_KEYS, 0.0))
  _parse_stage_hold_stack.set(None)
  wait_telem.set_enabled(_parse_stage_telem_on)
  wait_telem.begin_file()


def snapshot_parse_stage_timing() -> dict[str, float]:
  """
  Return per-file stage seconds from the current thread ContextVar.

  Returns:
    dict[str, float]: Empty when disabled; else every hold key plus derived
      ``build_df_s`` and ``stages_sum_s`` (zeros allowed).

  Examples:
    >>> reset_parse_stage_timing(enabled=False)
    >>> snapshot_parse_stage_timing()
    {}
  """
  if not _parse_stage_telem_on:
    return {}
  return _parse_stage_derived(_parse_stage_acc())


def _flush_parse_stage_campaign_from_file_acc() -> None:
  """
  Add unflushed per-file stage seconds into process-wide campaign totals.

  Takes ``_parse_stage_campaign_lock`` once. Idempotent via a per-context
  watermark so repeated snapshot/outcome flushes do not double-count.

  Returns:
    None

  Examples:
    >>> reset_parse_stage_timing(enabled=True)
    >>> _add_parse_stage_s("feed_s", 0.1)
    >>> _flush_parse_stage_campaign_from_file_acc()
  """
  if not _parse_stage_telem_on:
    return
  acc = _parse_stage_acc()
  flushed = _parse_stage_flushed_acc()
  with _parse_stage_campaign_lock:
    for key in PARSE_STAGE_HOLD_KEYS:
      total = float(acc.get(key, 0.0))
      already = float(flushed.get(key, 0.0))
      delta = total - already
      if delta <= 0.0:
        continue
      _parse_stage_campaign[key] = (
        float(_parse_stage_campaign.get(key, 0.0)) + delta
      )
      flushed[key] = total


def snapshot_parse_stage_campaign_timing() -> dict[str, float]:
  """
  Return process-wide parse-stage totals accumulated across ingest threads.

  Flushes the calling context's unflushed per-file accumulator first so
  closed-book E2 sees worker holds without a per-line campaign lock.

  Returns:
    dict[str, float]: Empty when disabled; else hold keys plus derived sums.

  Examples:
    >>> reset_parse_stage_timing(enabled=False)
    >>> snapshot_parse_stage_campaign_timing()
    {}
  """
  if not _parse_stage_telem_on:
    return {}
  _flush_parse_stage_campaign_from_file_acc()
  with _parse_stage_campaign_lock:
    return _parse_stage_derived(_parse_stage_campaign)


def attach_parse_unaccounted(meta: dict[str, Any]) -> dict[str, Any]:
  """
  Set ``parse_unaccounted_s`` from parse wall minus holds minus ``postgres_s``.

  Args:
    meta (dict[str, Any]): Outcome meta that may already include stage keys.

  Returns:
    dict[str, Any]: Same mapping; adds ``parse_unaccounted_s`` when stages and
      ``parse_elapsed_s`` are present.

  Examples:
    >>> attach_parse_unaccounted(
    ...   {"parse_elapsed_s": 10.0, "stages_sum_s": 3.0, "postgres_s": 2.0},
    ... )["parse_unaccounted_s"]
    5.0
  """
  if "stages_sum_s" not in meta or meta.get("parse_elapsed_s") is None:
    return meta
  parse_s = float(meta["parse_elapsed_s"])
  stages_sum = float(meta.get("stages_sum_s") or 0.0)
  postgres_s = float(meta.get("postgres_s") or 0.0)
  meta["parse_unaccounted_s"] = max(0.0, parse_s - stages_sum - postgres_s)
  return meta


def _add_parse_stage_s(stage: str, delta_s: float) -> None:
  """
  Accumulate non-negative seconds into the per-file ContextVar only.

  Campaign totals update later via ``_flush_parse_stage_campaign_from_file_acc``
  (outermost hold exit, campaign snapshot, or file-outcome merge). Never take
  ``_parse_stage_campaign_lock`` on this hot path.

  Args:
    stage (str): A key in ``PARSE_STAGE_HOLD_KEYS``.
    delta_s (float): Hold duration in seconds (non-positive values ignored).

  Returns:
    None

  Examples:
    >>> reset_parse_stage_timing(enabled=True)
    >>> _add_parse_stage_s("feed_s", 0.25)
  """
  if not _parse_stage_telem_on:
    return
  delta = float(delta_s)
  if delta <= 0.0 or stage not in PARSE_STAGE_HOLD_KEYS:
    return
  acc = _parse_stage_acc()
  acc[stage] = float(acc.get(stage, 0.0)) + delta


@contextmanager
def _held_parse_stage(stage: str) -> Iterator[None]:
  """
  Hold exclusive ``perf_counter`` for ``stage`` when telemetry is enabled.

  Nested enters pause the outer stage clock and resume it on exit so
  ``feed_s`` / ``proc_merge_s`` do not double-count wall. On outermost exit,
  flush unflushed per-file seconds into the process campaign once.

  Args:
    stage (str): A key in ``PARSE_STAGE_HOLD_KEYS``.

  Yields:
    None

  Examples:
    >>> reset_parse_stage_timing(enabled=False)
    >>> with _held_parse_stage("feed_s"):
    ...   pass
  """
  if not _parse_stage_telem_on:
    yield
    return
  stack = _parse_stage_hold_stack.get()
  if stack is None:
    stack = []
    _parse_stage_hold_stack.set(stack)
  now, cpu_now = time.perf_counter(), time.thread_time()
  if stack:
    outer_stage, outer_t0, outer_c0 = stack[-1]
    if outer_t0 is not None:
      _add_parse_stage_s(outer_stage, now - outer_t0)
      wait_telem.add_stage_cpu(outer_stage, cpu_now - outer_c0)
    stack[-1] = (outer_stage, None, 0.0)
  stack.append((stage, now, cpu_now))
  try:
    yield
  finally:
    end, cpu_end = time.perf_counter(), time.thread_time()
    cur_stage, t0, c0 = stack.pop()
    if t0 is not None:
      _add_parse_stage_s(cur_stage, end - t0)
      wait_telem.add_stage_cpu(cur_stage, cpu_end - c0)
    if stack:
      outer_stage, _paused, _c = stack[-1]
      stack[-1] = (outer_stage, end, cpu_end)
    else:
      _flush_parse_stage_campaign_from_file_acc()


# Instantaneous gauges and kernel peaks retained as high-water marks across
# samples / upserts for the same ``(jid, host, proc)`` name. Includes kernel
# VmPeak/VmHWM so a later zero or lower sample (or new PID same name) cannot
# erase the job-level high water.
HOST_PROC_PEAK_KEYS = frozenset(
  {
    "vm_peak",
    "vm_hwm",
    "vm_stk",
    "vm_exe",
    "vm_lib",
  }
)


def schema_key_basename(token: str) -> str:
  """
  Strip monitor schema option suffixes (``,U=kB``, ``,E``, ...).

  Args:
    token (str): Full schema entry from a ``!host_proc`` line (or bare key).

  Returns:
    str: Key basename before the first comma (empty string when ``token`` empty).

  Examples:
    >>> schema_key_basename("vm_peak,U=kB")
    'vm_peak'
    >>> schema_key_basename("uid")
    'uid'
    >>> schema_key_basename("vm_rss,E,U=kB")
    'vm_rss'
  """
  if not token:
    return ""
  return token.split(",", 1)[0]


def _nullable_int_max(left: Any, right: Any) -> Any:
  """
  Return the greater of two nullable integer-like values.

  Args:
    left (Any): First candidate (``None`` or int-like).
    right (Any): Second candidate (``None`` or int-like).

  Returns:
    Any: ``None`` when both missing; otherwise the max of convertible ints,
    or the sole non-``None`` side when the other cannot convert.

  Examples:
    >>> _nullable_int_max(None, 5)
    5
    >>> _nullable_int_max(10, 3)
    10
    >>> _nullable_int_max(None, None) is None
    True
  """
  if type(left) is int and type(right) is int:
    return left if left >= right else right
  left_ok: int | None
  right_ok: int | None
  try:
    left_ok = None if left is None else int(left)
  except TypeError, ValueError:
    left_ok = None
  try:
    right_ok = None if right is None else int(right)
  except TypeError, ValueError:
    right_ok = None
  if left_ok is None:
    return right_ok
  if right_ok is None:
    return left_ok
  return max(left_ok, right_ok)


def merge_proc_row_dicts(
  earlier: dict[str, Any],
  later: dict[str, Any],
) -> dict[str, Any]:
  """
  Merge two host_proc row dicts for the same ``(jid, host, proc)``.

  Non-peak fields take ``later`` (last-write). Peak keys
  (``HOST_PROC_PEAK_KEYS``) take the max of non-null values in the
  same ``later.items()`` pass. Integer peaks compare inline; every
  other peak value uses ``_nullable_int_max``. A peak absent from
  ``later`` is left untouched. Mutates and returns ``earlier``.

  Args:
    earlier (dict[str, Any]): Prior sample for the unique key (mutated).
    later (dict[str, Any]): Newer sample (last-write source).

  Returns:
    dict[str, Any]: ``earlier`` after merge; peak fields are GREATEST.

  Examples:
    >>> merge_proc_row_dicts(
    ...   {"vm_stk": 100, "vm_peak": 900, "threads": 1},
    ...   {"vm_stk": 50, "vm_peak": 800, "threads": 4},
    ... )["vm_stk"]
    100
    >>> merge_proc_row_dicts(
    ...   {"vm_stk": 100, "vm_peak": 900, "threads": 1},
    ...   {"vm_stk": 50, "vm_peak": 800, "threads": 4},
    ... )["vm_peak"]
    900
    >>> merge_proc_row_dicts(
    ...   {"vm_hwm": 7000, "threads": 1},
    ...   {"vm_hwm": 0, "threads": 4},
    ... )["vm_hwm"]
    7000
  """
  peaks = HOST_PROC_PEAK_KEYS
  for key, value in later.items():
    if key in peaks:
      prev = earlier.get(key)
      if type(prev) is int and type(value) is int:
        earlier[key] = prev if prev >= value else value
      else:
        earlier[key] = _nullable_int_max(prev, value)
    else:
      earlier[key] = value
  return earlier


def dedupe_proc_stats_peak_merge(
  proc_stats_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
  """
  Collapse duplicate ``(jid, host, proc)`` rows with peak-aware merge.

  Args:
    proc_stats_list (list[dict[str, Any]]): Parsed host_proc rows in time order.

  Returns:
    list[dict[str, Any]]: One row per unique key; peaks retained across samples.

  Examples:
    >>> rows = dedupe_proc_stats_peak_merge(
    ...   [
    ...     {
    ...       "jid": "j",
    ...       "host": "h",
    ...       "proc": "p",
    ...       "vm_stk": 9,
    ...       "threads": 1,
    ...     },
    ...     {
    ...       "jid": "j",
    ...       "host": "h",
    ...       "proc": "p",
    ...       "vm_stk": 3,
    ...       "threads": 8,
    ...     },
    ...   ]
    ... )
    >>> rows[0]["vm_stk"], rows[0]["threads"]
    (9, 8)
  """
  by_key: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
  for row in proc_stats_list:
    key = (row.get("jid"), row.get("host"), row.get("proc"))
    if key in by_key:
      by_key[key] = merge_proc_row_dicts(by_key[key], row)
    else:
      # Take ownership - callers must not mutate ``row`` after pass-in.
      by_key[key] = row
  return list(by_key.values())


class OnlineMergedProcRows(list):
  """
  ``list`` subclass marking host_proc rows already peak-merged online.

  ``build_stats_dataframes`` skips timed ``dedupe_proc_stats_peak_merge`` for
  this type. Ordinary ``list`` inputs still run batch dedupe.
  """


def _proc_rows_to_columns(
  rows: Sequence[dict[str, Any]],
) -> dict[str, list[Any]]:
  """
  Convert sparse host_proc row dicts into a columnar SoA payload.

  Args:
    rows (Sequence[dict[str, Any]]): Peak-merged (or raw) host_proc rows.

  Returns:
    dict[str, list[Any]]: Column name to value lists (aligned by row index).

  Examples:
    >>> _proc_rows_to_columns([{"proc": "p", "vm_peak": 1}])["proc"]
    ['p']
  """
  if not rows:
    return {}
  keys: list[str] = []
  seen: set[str] = set()
  for row in rows:
    for key in row:
      if key not in seen:
        seen.add(key)
        keys.append(key)
  cols: dict[str, list[Any]] = {key: [] for key in keys}
  for row in rows:
    for key in keys:
      cols[key].append(row.get(key))
  return cols


def apply_proc_peak_attrs_from_earlier(earlier: Any, later: Any) -> Any:
  """
  Copy GREATEST peak attrs from ``earlier`` onto ``later`` (ORM or namespace).

  Args:
    earlier (Any): Prior object with optional peak KEYS attrs
      (``vm_peak`` / ``vm_hwm`` / ``vm_stk`` / ``vm_exe`` / ``vm_lib``).
    later (Any): Incoming object mutated in place (last-write for other fields).

  Returns:
    Any: The ``later`` object after peak fields are raised when needed.

  Examples:
    >>> from types import SimpleNamespace
    >>> a = SimpleNamespace(
    ...   vm_peak=9000, vm_hwm=7000, vm_stk=10, vm_exe=1, vm_lib=2
    ... )
    >>> b = SimpleNamespace(
    ...   vm_peak=0, vm_hwm=100, vm_stk=3, vm_exe=9, vm_lib=None
    ... )
    >>> apply_proc_peak_attrs_from_earlier(a, b).vm_peak
    9000
    >>> b.vm_hwm
    7000
    >>> b.vm_stk
    10
    >>> b.vm_exe
    9
  """
  for key in HOST_PROC_PEAK_KEYS:
    cur = getattr(later, key, None)
    prev = getattr(earlier, key, None)
    setattr(later, key, _nullable_int_max(prev, cur))
  return later


def peak_merge_proc_objs_with_existing(
  proc_objs: list,
  *,
  lookup_chunk_size: int | None = None,
) -> list:
  """
  Raise peak fields on ``proc_objs`` from matching ``proc_data`` DB rows.

  Groups by ``(jid, host)`` and loads existing rows with ``proc__in`` slices
  of ``lookup_chunk_size`` (INI ``listend_db_ingest_proc_peak_lookup_chunk``,
  default 256) so MPI-scale batches do not build one giant OR query.

  Args:
    proc_objs (list): ``proc_data`` instances about to be upserted.
    lookup_chunk_size (int | None): Override INI chunk size; ``None`` reads
      ``get_listend_db_ingest_proc_peak_lookup_chunk()``.

  Returns:
    list: Same list (objs mutated in place when a DB peer exists).

  Examples:
    >>> peak_merge_proc_objs_with_existing([])
    []
  """
  if not proc_objs:
    return proc_objs

  from hpcperfstats.site.lib.machine.models import proc_data

  chunk_size = lookup_chunk_size
  if chunk_size is None:
    try:
      import hpcperfstats.dbload.lib.conf_parser as cfg

      chunk_size = int(cfg.get_listend_db_ingest_proc_peak_lookup_chunk())
    except Exception:
      chunk_size = 256
  chunk_size = max(1, int(chunk_size))

  existing: dict[tuple[Any, Any, Any], Any] = {}
  by_jh: dict[tuple[Any, Any], list] = {}
  for obj in proc_objs:
    by_jh.setdefault((obj.jid, obj.host), []).append(obj)

  for (jid, host), group in by_jh.items():
    seen: set[Any] = set()
    procs: list[Any] = []
    for obj in group:
      if obj.proc in seen:
        continue
      seen.add(obj.proc)
      procs.append(obj.proc)
    for i in range(0, len(procs), chunk_size):
      part = procs[i : i + chunk_size]
      for row in proc_data.objects.filter(
        jid=jid, host=host, proc__in=part
      ).only("jid", "host", "proc", *HOST_PROC_PEAK_KEYS):
        existing[(row.jid, row.host, row.proc)] = row

  if not existing:
    return proc_objs
  for obj in proc_objs:
    prior = existing.get((obj.jid, obj.host, obj.proc))
    if prior is not None:
      apply_proc_peak_attrs_from_earlier(prior, obj)
  return proc_objs


# Back-compat re-export for callers/tests that referenced legacy eventmaps.
EVENTMAPS_BY_TYPE = legacy_parsing.EVENTMAPS_BY_TYPE
map_hardware_counter_vals = legacy_parsing.map_hardware_counter_vals

_NVIDIA_GPU_SUM_EVENTS = frozenset(
  {
    "gpu_util",
    "gpu_io_link_total_bytes",
    "mem_util",
    "mem_used_mb",
    "mem_total_mb",
    "gpu_mem_util",
    "gpu_mem_used_mb",
    "gpu_mem_total_mb",
    "fp64_active",
    "fp32_active",
    "fp16_active",
    "sm_active",
    "sm_occupancy",
    "tensor_active",
    "power_usage",
  }
)
_NVIDIA_GPU_MAX_EVENTS = frozenset(
  {
    "module_power_usage",
    "sysio_power_usage",
    # Node GPU count is emitted on every device row; MAX avoids NxN when
    # identity collapses without a distinct ``dev`` (legacy / empty-dev path).
    "gpu_count",
  }
)
_NVIDIA_GPU_MEAN_EVENTS = frozenset({"temperature"})
_NVIDIA_GPU_OR_EVENTS = frozenset({"clocks_event_reasons"})

_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS = frozenset(
  {
    DCGM_CPU_POWER_UTIL_W,
    DCGM_CPU_POWER_LIMIT_W,
    "DCGM_CPU_POWER_UTIL_W",
    "DCGM_CPU_POWER_LIMIT_W",
  }
)

_HOST_CPU_HW_TYPES = frozenset({HOST_CPU_HW_TYPE, LEGACY_HOST_CPU_HW_TYPE})
_GPU_STATS_TYPES = frozenset({"nvidia_gpu", "amd_gpu", "intel_gpu"})
# Job-scoped cgroup memory (monitor ``host_mem_job``); always ``dev=j:<jid>``.
_HOST_MEM_JOB_TYPE = "host_mem_job"

# Non-GPU collapse drops hardware ``dev`` (sum across devices) unless the
# token is a shared-node job stamp ``j:<jid>``, which stays in the group key.
_COLLAPSE_GROUP_COLS = ["host", "type", "event", "unit", "time"]
# GPU types and ``j:`` job stamps keep ``dev`` so same-second jobs do not merge.
_COLLAPSE_GROUP_COLS_WITH_DEV = ["host", "type", "dev", "event", "unit", "time"]
_COUNTER_GROUP_COLS = ["host", "type", "dev", "event"]
# Arc continuity must match counter grain (include ``dev`` for multi-GPU).
_ARC_GROUP_COLS = ["host", "type", "dev", "event"]
_NVIDIA_GROUP_KEY_EVENT_INDEX = _COLLAPSE_GROUP_COLS_WITH_DEV.index("event")

_SLOW_TIER_OPT = "R=S"
_TIER_MARKERS = frozenset({"@fast", "@full"})


def _schema_token_is_slow_tier(token: str) -> bool:
  """
  True when a schema entry is marked slow-tier via ,R=S (monitor two-tier.

    collect).

  Args:
    token (str): String for token.

  Returns:
    bool: True or False for this check.

  Examples:
    >>> _schema_token_is_slow_tier("x")  # doctest: +SKIP
  """
  return _SLOW_TIER_OPT in token.split(",")[1:]


def _fast_schema_keys(full_events: list[str]) -> list[str]:
  """
  Fast-tier schema keys in order (entries without ,R=S).

  Args:
    full_events (list[str]): Sequence for full events.

  Returns:
    list[str]: list[str] produced by this call.

  Examples:
    >>> _fast_schema_keys([])  # doctest: +SKIP
  """
  return [e for e in full_events if not _schema_token_is_slow_tier(e)]


_STATS_COL_NAMES = (
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "value",
  "wid",
  "mult",
  "unit",
)


def _empty_stats_columns() -> dict[str, list]:
  """
  Return empty parallel lists for hardware stats emit.

  Returns:
    dict[str, list]: Column name to empty list for each stats field.

  Examples:
    >>> cols = _empty_stats_columns()
    >>> cols["time"]
    []
    >>> set(cols) == set(_STATS_COL_NAMES)
    True
  """
  return {name: [] for name in _STATS_COL_NAMES}


def _compile_schema_token(token: str) -> tuple[str, int, float, str]:
  """
  Compile one ``!`` schema token into event name, width, multiplier, unit.

  Args:
    token (str): Schema entry such as ``CAS_READS,W=48,U=64B``.

  Returns:
    tuple[str, int, float, str]: ``(event, wid, mult, unit)`` with the same
      defaults as the former per-row ``W=`` / ``U=`` walk (wid 64, mult 1,
      unit ``#``).

  Examples:
    >>> _compile_schema_token("user")
    ('user', 64, 1, '#')
    >>> _compile_schema_token("CAS_READS,W=48,U=64B")
    ('CAS_READS', 48, 64.0, 'B')
  """
  eve_parts = token.split(",")
  width = 64
  mult: float | int = 1
  unit = "#"
  for ele in eve_parts[1:]:
    if "W=" in ele:
      width = int(ele.lstrip("W="))
    if "U=" in ele:
      ele = ele.lstrip("U=")
      with suppress(Exception):
        mult = float("".join(filter(str.isdigit, ele)))
      with suppress(Exception):
        unit = "".join(filter(str.isalpha, ele))
  return eve_parts[0], width, mult, unit


def _empty_compiled_schema() -> dict[str, list]:
  """
  Return empty parallel arrays for a compiled ``!`` schema.

  Returns:
    dict[str, list]: ``events`` / ``wids`` / ``mults`` / ``units`` lists.

  Examples:
    >>> _empty_compiled_schema()["events"]
    []
  """
  return {"events": [], "wids": [], "mults": [], "units": []}


def _compile_schema_tokens(
  tokens: list[str],
) -> dict[str, list]:
  """
  Compile an ordered ``!`` schema key list into parallel arrays.

  Args:
    tokens (list[str]): Schema tokens for one hardware type.

  Returns:
    dict[str, list]: SoA with ``events``, ``wids``, ``mults``, ``units``
      aligned with ``tokens`` (no per-line ``zip(*compiled)`` at emit).

  Examples:
    >>> _compile_schema_tokens(["user,W=48", "sys"])["events"]
    ['user', 'sys']
    >>> _compile_schema_tokens(["user,W=48", "sys"])["wids"]
    [48, 64]
  """
  out = _empty_compiled_schema()
  for token in tokens:
    event, wid, mult, unit = _compile_schema_token(token)
    out["events"].append(event)
    out["wids"].append(wid)
    out["mults"].append(mult)
    out["units"].append(unit)
  return out


def _compile_schema_bare_names(tokens: list[str]) -> list[str]:
  """
  Compile schema token basenames once for proc emit.

  Args:
    tokens (list[str]): Schema tokens (may include ``,U=`` / ``,E`` suffixes).

  Returns:
    list[str]: Bare key names aligned with ``tokens``.

  Examples:
    >>> _compile_schema_bare_names(["vm_peak,U=kB", "threads"])
    ['vm_peak', 'threads']
  """
  return [schema_key_basename(token) for token in tokens]


def stats_payload_row_count(payload: Any) -> int:
  """
  Return parsed hardware row count for a list of dicts or column dict.

  Zero-host archive marks must use this parsed count, not post-collapse
  lengths.

  Args:
    payload (Any): Columnar ``dict[str, list]``, sequence of row dicts, or
      empty/``None``.

  Returns:
    int: Number of hardware stats rows.

  Examples:
    >>> stats_payload_row_count({"time": [1.0, 2.0]})
    2
    >>> stats_payload_row_count([{"time": 1.0}])
    1
    >>> stats_payload_row_count(None)
    0
  """
  if payload is None:
    return 0
  if isinstance(payload, dict):
    times = payload.get("time")
    return len(times) if times is not None else 0
  return len(payload)


def stats_payload_to_records(payload: Any) -> list[dict[str, Any]]:
  """
  Convert columnar stats or row dicts into a list of row dicts.

  Production builders take columns; tests and ``parse_stats_lines`` use this
  adapter.

  Args:
    payload (Any): Columnar ``dict[str, list]``, sequence of row dicts, or
      empty/``None``.

  Returns:
    list[dict[str, Any]]: One dict per hardware event row.

  Examples:
    >>> stats_payload_to_records({"time": [1.0], "host": ["h"]})
    [{'time': 1.0, 'host': 'h'}]
    >>> stats_payload_to_records([{"time": 1.0}])
    [{'time': 1.0}]
  """
  if payload is None:
    return []
  if isinstance(payload, dict):
    n = stats_payload_row_count(payload)
    if n == 0:
      return []
    keys = [k for k, values in payload.items() if len(values) == n]
    return [{k: payload[k][i] for k in keys} for i in range(n)]
  return list(payload)


def _append_compiled_stats_columns(
  cols: dict[str, list],
  *,
  time: float,
  host: str,
  jid: str,
  typ: str,
  dev: str,
  compiled: dict[str, list],
  vals: list[str],
) -> None:
  """
  Append one hardware line into columnar lists using a compiled SoA schema.

  Args:
    cols (dict[str, list]): Parallel stats columns mutated in place.
    time (float): Sample unix seconds from the digit header.
    host (str): Hostname token from the digit header.
    jid (str): Jobid token from the digit header (``-`` when idle).
    typ (str): Output hardware type (legacy remapped when needed).
    dev (str): Device token from the stats line.
    compiled (dict[str, list]): SoA from ``_compile_schema_tokens`` aligned
      with ``vals``.
    vals (list[str]): Value tokens after an optional ``@fast``/``@full``
      marker.

  Returns:
    None

  Examples:
    >>> cols = _empty_stats_columns()
    >>> _append_compiled_stats_columns(
    ...   cols,
    ...   time=1.0,
    ...   host="h",
    ...   jid="j",
    ...   typ="cpu",
    ...   dev="0",
    ...   compiled={
    ...     "events": ["user"],
    ...     "wids": [48],
    ...     "mults": [1],
    ...     "units": ["#"],
    ...   },
    ...   vals=["10"],
    ... )
    >>> cols["event"], cols["value"]
    (['user'], [10.0])
  """
  events = compiled["events"]
  n = len(events)
  if len(vals) != n:
    warnings.warn(
      f"stats line value count {len(vals)} != schema key count {n} for type={typ} dev={dev}",
      stacklevel=3,
    )
    return
  cols["time"].extend((time,) * n)
  cols["host"].extend((host,) * n)
  cols["jid"].extend((jid,) * n)
  cols["type"].extend((typ,) * n)
  cols["dev"].extend((dev,) * n)
  cols["event"].extend(events)
  cols["wid"].extend(compiled["wids"])
  cols["mult"].extend(compiled["mults"])
  cols["unit"].extend(compiled["units"])
  cols["value"].extend([float(v) for v in vals])


def _decode_stats_readline(raw: bytes | str) -> str:
  """
  Decode one stats-file readline from bytes or str.

  Args:
    raw (bytes | str): Line as returned by text or binary ``readline``.

  Returns:
    str: ASCII text with ``errors=replace`` for non-ASCII bytes.

  Examples:
    >>> _decode_stats_readline(b"cpu 0 1\\n")
    'cpu 0 1\\n'
    >>> _decode_stats_readline("cpu 0 1\\n")
    'cpu 0 1\\n'
  """
  if isinstance(raw, bytes):
    return raw.decode("ascii", "replace")
  return raw


def _read_stats_line_batch_decode_after_lock(
  fd: Any,
  stats_file: str,
  batch_size: int,
) -> list[str]:
  """
  Read up to ``batch_size`` raw lines under SH; decode after release.

  Args:
    fd (Any): Binary file object open for the stats path.
    stats_file (str): Path used for ``_stats_file_read_lock``.
    batch_size (int): Max ``readline`` calls per lock hold.

  Returns:
    list[str]: Decoded lines (empty at EOF).

  Examples:
    >>> _read_stats_line_batch_decode_after_lock(None, "x", 1)  # doctest: +SKIP
  """
  raw_batch: list[bytes] = []
  with _held_parse_stage("lock_s"), _stats_file_read_lock(stats_file):
    for _ in range(int(batch_size)):
      raw = fd.readline()
      if not raw:
        break
      raw_batch.append(raw)
    if raw_batch:
      from hpcperfstats.dbload.lib.zstd_cli import (
        drop_page_cache_for_fd,
      )

      chunk_bytes = sum(len(raw) for raw in raw_batch)
      if chunk_bytes > 0:
        end_pos = fd.tell()
        drop_page_cache_for_fd(fd, end_pos - chunk_bytes, chunk_bytes)
  with _held_parse_stage("decode_s"):
    return [_decode_stats_readline(raw) for raw in raw_batch]


def _zip_schema_vals(
  schema_keys: Any,
  vals: Any,
  typ: Any | None = None,
  dev: Any | None = None,
) -> Any:
  """
  Zip value tokens to schema keys; None when counts disagree (no silent.

    truncation).

  Args:
    schema_keys (Any): Schema keys passed to this helper.
    vals (Any): Vals passed to this helper.
    typ (Any | None): One of ``Any``, ``None``.
    dev (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _zip_schema_vals(None, None, None, None)  # doctest: +SKIP
  """
  if len(vals) != len(schema_keys):
    warnings.warn(
      f"stats line value count {len(vals)} != schema key count {len(schema_keys)} for type={typ} dev={dev}",
      stacklevel=3,
    )
    return None
  return dict(zip(schema_keys, vals, strict=False))


def _cluster_mean_sum_sorted(values: Any, gap_threshold: Any) -> Any:
  """
  Internal helper to handle cluster mean sum sorted.

  Args:
    values (Any): Values passed to this helper.
    gap_threshold (Any): Gap threshold passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _cluster_mean_sum_sorted(None, None)  # doctest: +SKIP
  """
  v = np.asarray(values, dtype=np.float64)
  v = nan_out_dcgm_numeric_blanks(v)
  v = v[np.isfinite(v)]
  if v.size == 0:
    return float("nan")
  v.sort()
  if v.size == 1:
    return float(v[0])
  split_idx = np.flatnonzero(np.diff(v) > gap_threshold) + 1
  starts = np.empty(split_idx.size + 1, dtype=np.intp)
  starts[0] = 0
  starts[1:] = split_idx
  sums = np.add.reduceat(v, starts)
  counts = np.diff(np.append(starts, v.size))
  return float(np.sum(sums / counts))


def _dcg_delta_gap_threshold(dvals: Any) -> Any:
  """
  Dynamic delta clustering gap for DCGM CPU power gauge collapse.

  Args:
    dvals (Any): Dvals passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _dcg_delta_gap_threshold(None)  # doctest: +SKIP
  """
  d_gap = 1e-6
  finite = dvals[np.isfinite(dvals)]
  if finite.size == 0:
    return d_gap
  dabs = np.nanmax(np.abs(finite))
  if np.isfinite(dabs) and dabs > 0:
    d_gap = max(1e-9, 0.05 * float(dabs))
  return d_gap


def _collapse_dcg_cpu_power_gauge_group(group: Any) -> Any:
  """
  Apply-reference DCGM collapse; production path uses vectorized helper.

  Args:
    group (Any): Group passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _collapse_dcg_cpu_power_gauge_group(None)  # doctest: +SKIP
  """
  vals = group["value"].to_numpy(dtype=np.float64, copy=False)
  dvals = group["delta"].to_numpy(dtype=np.float64, copy=False)
  vtot = _cluster_mean_sum_sorted(vals, 1.0)
  dtot = _cluster_mean_sum_sorted(dvals, _dcg_delta_gap_threshold(dvals))
  return pd.Series({"value": vtot, "delta": dtot})


def _collapse_dcg_cpu_power_vectorized(ccm_df: Any, gcols: Any) -> Any:
  """
  Collapse DCGM CPU power gauges via explicit group loop (not groupby.apply).

  Args:
    ccm_df (Any): Ccm df passed to this helper.
    gcols (Any): Gcols passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _collapse_dcg_cpu_power_vectorized(None, None)  # doctest: +SKIP
  """
  rows = []
  for key, group in ccm_df.groupby(gcols, observed=True, sort=False):
    vals = group["value"].to_numpy(dtype=np.float64, copy=False)
    dvals = group["delta"].to_numpy(dtype=np.float64, copy=False)
    key_tuple = key if isinstance(key, tuple) else (key,)
    row = dict(zip(gcols, key_tuple, strict=False))
    row["value"] = _cluster_mean_sum_sorted(vals, 1.0)
    row["delta"] = _cluster_mean_sum_sorted(
      dvals, _dcg_delta_gap_threshold(dvals)
    )
    if "jid" in group.columns and len(group):
      row["jid"] = group["jid"].iloc[0]
    rows.append(row)
  if not rows:
    return _empty_delta_arc_frame()
  return DataFrame(rows)


def _collapse_nvidia_gpu_group(group: Any) -> Any:
  """
  Apply-reference NVIDIA collapse; production path uses vectorized helper.

  Args:
    group (Any): Group passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _collapse_nvidia_gpu_group(None)  # doctest: +SKIP
  """
  # Prefer the event column so this works with/without ``dev`` in the group key.
  if "event" in group.columns and len(group):
    event_name = group["event"].iloc[0]
  else:
    key = group.name
    event_name = (
      key[_NVIDIA_GROUP_KEY_EVENT_INDEX] if isinstance(key, tuple) else key
    )
  group = group.copy()
  group["value"] = nan_out_dcgm_numeric_blanks(
    group["value"].to_numpy(dtype=np.float64)
  )
  if event_name in _NVIDIA_GPU_MAX_EVENTS:
    return pd.Series(
      {
        "value": float(group["value"].max()),
        "delta": group["delta"].mean(),
      }
    )
  if event_name in _NVIDIA_GPU_SUM_EVENTS:
    return pd.Series(
      {
        "value": group["value"].sum(min_count=1),
        "delta": group["delta"].sum(min_count=1),
      }
    )
  if event_name in _NVIDIA_GPU_MEAN_EVENTS:
    return pd.Series(
      {
        "value": group["value"].mean(),
        "delta": group["delta"].mean(),
      }
    )
  if event_name in _NVIDIA_GPU_OR_EVENTS:
    acc = 0
    mask64 = (1 << 64) - 1
    for v in group["value"]:
      if pd.notna(v) and not is_dcgm_numeric_blank(v):
        acc |= int(v) & mask64
    return pd.Series(
      {
        "value": float(acc & mask64),
        "delta": group["delta"].sum(min_count=1),
      }
    )
  return pd.Series(
    {
      "value": group["value"].sum(min_count=1),
      "delta": group["delta"].sum(min_count=1),
    }
  )


_NVIDIA_GPU_KNOWN_EVENTS = frozenset().union(
  _NVIDIA_GPU_SUM_EVENTS,
  _NVIDIA_GPU_MAX_EVENTS,
  _NVIDIA_GPU_MEAN_EVENTS,
  _NVIDIA_GPU_OR_EVENTS,
)
# Single-pass event->class map for NVIDIA collapse (0=sum/default, 1=max,
# 2=mean, 3=OR). Unknown events stay sum/default (legacy behavior).
_NVIDIA_EVENT_CLASS_SUM = 0
_NVIDIA_EVENT_CLASS_MAX = 1
_NVIDIA_EVENT_CLASS_MEAN = 2
_NVIDIA_EVENT_CLASS_OR = 3
_NVIDIA_EVENT_TO_CLASS: dict[str, int] = {
  **dict.fromkeys(_NVIDIA_GPU_SUM_EVENTS, _NVIDIA_EVENT_CLASS_SUM),
  **dict.fromkeys(_NVIDIA_GPU_MAX_EVENTS, _NVIDIA_EVENT_CLASS_MAX),
  **dict.fromkeys(_NVIDIA_GPU_MEAN_EVENTS, _NVIDIA_EVENT_CLASS_MEAN),
  **dict.fromkeys(_NVIDIA_GPU_OR_EVENTS, _NVIDIA_EVENT_CLASS_OR),
}


def _nvidia_bitwise_or_values(series: Any) -> Any:
  """
  Bitwise OR of finite non-blank ``clocks_event_reasons`` within one collapse.

    group.

  Args:
    series (Any): Series passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _nvidia_bitwise_or_values(pd.Series([1.0, 2.0]))
    3.0
  """
  vals = np.asarray(series, dtype=np.float64)
  finite = np.isfinite(vals) & (vals < DCGM_FP64_BLANK)
  if not finite.any():
    return 0.0
  ints = vals[finite].astype(np.uint64, copy=False)
  mask64 = np.uint64((1 << 64) - 1)
  return float(int(np.bitwise_or.reduce(ints) & mask64))


def _optional_jid_first_agg(df: Any) -> dict[str, tuple[str, str]]:
  """
  Return ``jid`` ``first`` agg kwargs when the column is present.

  Sample-header jobid is constant within a collapse group; do not add ``jid``
  to group keys (DB uniqueness remains time/host/type/event[/dev]).

  Args:
    df (Any): Stats frame that may include a ``jid`` column.

  Returns:
    dict[str, tuple[str, str]]: Empty or ``{"jid": ("jid", "first")}``.

  Examples:
    >>> _optional_jid_first_agg(DataFrame({"jid": ["1"]}))
    {'jid': ('jid', 'first')}
  """
  if "jid" in getattr(df, "columns", ()):
    return {"jid": ("jid", "first")}
  return {}


def _groupby_sum_min_count(
  df: Any,
  gcols: Any,
  *,
  assume_duplicates: bool = False,
) -> Any:
  """
  Sum value/delta across devs with pandas ``sum(min_count=1)`` NaN semantics.

  When every ``gcols`` key is unique (common for GPU-with-dev and many
  host metrics), skip ``groupby`` factorize - return the projection.
  Host multi-dev collapse drops ``dev`` from ``gcols``, so callers that
  already know rows duplicate should pass ``assume_duplicates=True`` to
  skip the wasted uniqueness scan and cast keys to ``category`` before
  ``groupby`` (production Horizon path).

  Args:
    df (Any): Df passed to this helper.
    gcols (Any): Gcols passed to this helper.
    assume_duplicates (bool): Skip identity short-circuit; cast categories.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _groupby_sum_min_count(None, None)  # doctest: +SKIP
  """
  if df.empty:
    return _empty_delta_arc_frame()
  # Identity groups: groupby.factorize dominates Horizon-sized unique frames.
  if not assume_duplicates and not df.duplicated(gcols).any():
    keep = [*list(gcols), "value", "delta"]
    if "jid" in getattr(df, "columns", ()):
      keep.append("jid")
    return df.loc[:, keep].reset_index(drop=True)
  for col in gcols:
    if col == "time" or col not in getattr(df, "columns", ()):
      continue
    if not isinstance(df[col].dtype, pd.CategoricalDtype):
      df[col] = df[col].astype("category")
  grouped = df.groupby(gcols, observed=True, sort=False)
  out = grouped[["value", "delta"]].sum(min_count=1)
  if "jid" in getattr(df, "columns", ()):
    out = out.join(grouped["jid"].first())
  return out.reset_index()


def _nvidia_nan_out_dcgm_blanks(nv_df: Any) -> Any:
  """
  Replace DCGM blank-family ``value`` entries with NaN before NVIDIA collapse.

  Args:
    nv_df (Any): Nv df passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _nvidia_nan_out_dcgm_blanks(None)  # doctest: +SKIP
  """
  if nv_df.empty or "value" not in nv_df.columns:
    return nv_df
  vals = nv_df["value"].to_numpy(dtype=np.float64, copy=False)
  # FP64 blank base also excludes INT64 blank family (larger magnitude).
  blank = np.isfinite(vals) & (vals >= DCGM_FP64_BLANK)
  if not blank.any():
    return nv_df
  cleaned = nan_out_dcgm_numeric_blanks(vals, copy=True)
  out = nv_df.copy()
  out["value"] = cleaned
  return out


def _collapse_nvidia_gpu_vectorized(nv_df: Any, gcols: Any) -> Any:
  """
  Collapse NVIDIA GPU metrics via native groupby aggregations (not
  groupby.apply).

  Partitions event classes with one ``map`` pass (not four ``isin`` scans)
  and casts non-time group keys to ``category`` before groupby. Unique
  group keys return the projected rows and mask OR values in one array
  pass; duplicate keys keep the per-class aggregations.

  Args:
    nv_df (Any): Nv df passed to this helper.
    gcols (Any): Gcols passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _collapse_nvidia_gpu_vectorized(None, None)  # doctest: +SKIP
  """
  nv_df = _nvidia_nan_out_dcgm_blanks(nv_df)
  if nv_df.empty:
    return _empty_delta_arc_frame()
  for col in gcols:
    if col == "time" or col not in nv_df.columns:
      continue
    if not isinstance(nv_df[col].dtype, pd.CategoricalDtype):
      nv_df[col] = nv_df[col].astype("category")
  # One map pass: unknown events -> sum/default (class 0).
  event_class = (
    nv_df["event"]
    .map(_NVIDIA_EVENT_TO_CLASS)
    .fillna(_NVIDIA_EVENT_CLASS_SUM)
    .to_numpy(dtype=np.int8, copy=False)
  )
  if not nv_df.duplicated(gcols).any():
    keep = [c for c in (*gcols, "value", "delta", "jid") if c in nv_df.columns]
    out = nv_df.loc[:, keep].reset_index(drop=True)
    or_rows = event_class == _NVIDIA_EVENT_CLASS_OR
    if or_rows.any():
      raw = out["value"].to_numpy(dtype=np.float64, copy=False)
      vals = raw.copy()
      finite = or_rows & np.isfinite(raw) & (raw < DCGM_FP64_BLANK)
      vals[or_rows] = 0.0
      if finite.any():
        vals[finite] = raw[finite].astype(np.uint64).astype(np.float64)
      out["value"] = vals
    return out
  parts = []
  sum_df = nv_df.loc[event_class == _NVIDIA_EVENT_CLASS_SUM]
  if not sum_df.empty:
    parts.append(_groupby_sum_min_count(sum_df, gcols))

  max_df = nv_df.loc[event_class == _NVIDIA_EVENT_CLASS_MAX]
  if not max_df.empty:
    parts.append(
      max_df.groupby(gcols, observed=True, sort=False)
      .agg(
        value=("value", "max"),
        delta=("delta", "mean"),
        **_optional_jid_first_agg(max_df),
      )
      .reset_index()
    )

  mean_df = nv_df.loc[event_class == _NVIDIA_EVENT_CLASS_MEAN]
  if not mean_df.empty:
    parts.append(
      mean_df.groupby(gcols, observed=True, sort=False)
      .agg(
        value=("value", "mean"),
        delta=("delta", "mean"),
        **_optional_jid_first_agg(mean_df),
      )
      .reset_index()
    )

  or_df = nv_df.loc[event_class == _NVIDIA_EVENT_CLASS_OR]
  if not or_df.empty:
    or_collapsed = (
      or_df.groupby(gcols, observed=True, sort=False)
      .agg(
        value=("value", _nvidia_bitwise_or_values),
        delta=("delta", "sum"),
        _delta_n=("delta", "count"),
        **_optional_jid_first_agg(or_df),
      )
      .reset_index()
    )
    or_collapsed["delta"] = or_collapsed["delta"].where(
      or_collapsed["_delta_n"] > 0
    )
    parts.append(or_collapsed.drop(columns=["_delta_n"]))

  if not parts:
    return _empty_delta_arc_frame()
  out = parts[0] if len(parts) == 1 else concat(parts, ignore_index=True)
  for col in gcols:
    if col == "time" or col not in out.columns:
      continue
    if not isinstance(out[col].dtype, pd.CategoricalDtype):
      out[col] = out[col].astype("category")
  return out


def _vals_dict_from_line(
  typ: Any,
  schema: Any,
  schema_keys: Any,
  vals: Any,
  use_legacy_decode: bool,
  dev: Any | None = None,
) -> Any:
  """
  Internal helper to handle vals dict from line.

  Args:
    typ (Any): Typ passed to this helper.
    schema (Any): Schema passed to this helper.
    schema_keys (Any): Schema keys passed to this helper.
    vals (Any): Vals passed to this helper.
    use_legacy_decode (bool): Whether to enable use legacy decode.
    dev (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _vals_dict_from_line(None, None, None, None, True, None)
  """
  if use_legacy_decode:
    decoded = legacy_parsing.decode_counter_line(typ, schema, vals)
    if decoded is None:
      return None
    return decoded
  return _zip_schema_vals(schema_keys, vals, typ=typ, dev=dev)


def _append_vals_dict_columns(
  cols: dict[str, list],
  *,
  time: float,
  host: str,
  jid: str,
  typ: str,
  dev: str,
  vals_dict: dict[str, Any],
) -> None:
  """
  Append a legacy decoded event map into columnar stats lists.

  Args:
    cols (dict[str, list]): Parallel stats columns mutated in place.
    time (float): Sample unix seconds from the digit header.
    host (str): Hostname token from the digit header.
    jid (str): Jobid token from the digit header (``-`` when idle).
    typ (str): Output hardware type after legacy remap.
    dev (str): Device token from the stats line.
    vals_dict (dict[str, Any]): Event token to numeric value from legacy
      decode.

  Returns:
    None

  Examples:
    >>> cols = _empty_stats_columns()
    >>> _append_vals_dict_columns(
    ...   cols,
    ...   time=1.0,
    ...   host="h",
    ...   jid="j",
    ...   typ="cpu",
    ...   dev="0",
    ...   vals_dict={"user,W=48": 10},
    ... )
    >>> cols["event"], cols["wid"], cols["value"]
    (['user'], [48], [10.0])
  """
  if not vals_dict:
    return
  compiled = _compile_schema_tokens(list(vals_dict.keys()))
  _append_compiled_stats_columns(
    cols,
    time=time,
    host=host,
    jid=jid,
    typ=typ,
    dev=dev,
    compiled=compiled,
    vals=[str(v) for v in vals_dict.values()],
  )


def parse_stats_file_path(stats_file: str) -> Any:
  """
  Parse the stats file path.

  Args:
    stats_file (str): String for stats file.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> parse_stats_file_path("x")  # doctest: +SKIP
  """
  parts = stats_file.split("/")
  if len(parts) >= 2:
    return parts[-2], parts[-1]
  return None, None


STREAM_PARSE_LINE_BATCH = 50000


def stats_file_size_bytes(stats_file: str) -> Any:
  """
  Return on-disk size in bytes (0 when missing or unreadable).

  Args:
    stats_file (str): String for stats file.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> stats_file_size_bytes("x")  # doctest: +SKIP
  """
  try:
    return int(os.path.getsize(stats_file))
  except OSError:
    return 0


_READ_LOOP_DEADLINE_EVERY_LINES = 1000
_READ_LOOP_DEADLINE_EVERY_BYTES = 1 << 20


def _maybe_raise_ingest_read_deadline(line_idx: Any, bytes_read: Any) -> None:
  """
  Check wall/idle ingest deadlines periodically during stats file reads.

  Also heartbeats progress so idle-stall does not fire while lines are
  advancing.

  Args:
    line_idx (Any): 1-based line count so far.
    bytes_read (Any): Cumulative bytes read so far.

  Returns:
    None

  Examples:
    >>> _maybe_raise_ingest_read_deadline(None, None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_progress import (
    raise_if_ingest_idle_stalled,
    touch_ingest_progress,
  )

  if line_idx and line_idx % _READ_LOOP_DEADLINE_EVERY_LINES == 0:
    touch_ingest_progress()
    raise_if_ingest_idle_stalled(stage="idle_stall")
  if bytes_read and bytes_read % _READ_LOOP_DEADLINE_EVERY_BYTES == 0:
    touch_ingest_progress()
    raise_if_ingest_idle_stalled(stage="idle_stall")


@contextmanager
def _stats_file_read_lock(stats_file: str) -> Iterator[None]:
  """Hold a stats-file read lock and unlink the fnctl sidecar afterward.

  Args:
    stats_file (str): Absolute or relative stats file path.

  Yields:
    None: Control returns to the caller while the lock is held.

  Examples:
    >>> import os, tempfile
    >>> fd, path = tempfile.mkstemp()
    >>> os.close(fd)
    >>> with _stats_file_read_lock(path):
    ...   os.path.isfile(path)
    True
    >>> os.remove(path)
  """
  try:
    with file_read_lock_wait(stats_file):
      yield
  finally:
    lock_path = f"{stats_file}{LOCK_SUFFIX}"
    with suppress(OSError):
      os.remove(lock_path)


def load_stats_file_lines(
  stats_file: str,
  stats_file_contents: Any | None = None,
) -> Any:
  """
  Load the stats file lines.

  Args:
    stats_file (str): String for stats file.
    stats_file_contents (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> load_stats_file_lines("x", None)  # doctest: +SKIP
  """
  if stats_file_contents is not None:
    return stats_file_contents, None
  lines = []
  bytes_read = 0
  try:
    with open(stats_file) as fd:
      line_idx = 0
      while True:
        batch: list[str] = []
        with _stats_file_read_lock(stats_file):
          for _ in range(int(STREAM_PARSE_LINE_BATCH)):
            line = fd.readline()
            if not line:
              break
            batch.append(line)
        if not batch:
          break
        for line in batch:
          line_idx += 1
          bytes_read += len(line)
          _maybe_raise_ingest_read_deadline(line_idx, bytes_read)
          lines.append(line)
    return lines, None
  except FileNotFoundError:
    return None, f"Stats file disappeared: {stats_file}"


def iter_stats_file_lines(stats_file: str) -> Iterator[Any]:
  """
  Yield lines from a stats file, holding the read lock per line batch.

  Args:
    stats_file (str): Path to a monitor stats file.

  Yields:
    str: Decoded stats-file lines in order.

  Examples:
    >>> list(iter_stats_file_lines("/missing/stats"))
    []
  """
  try:
    with open(stats_file, "rb") as fd:
      line_idx = 0
      bytes_read = 0
      while True:
        batch = _read_stats_line_batch_decode_after_lock(
          fd,
          stats_file,
          STREAM_PARSE_LINE_BATCH,
        )
        if not batch:
          break
        for line in batch:
          line_idx += 1
          bytes_read += len(line)
          _maybe_raise_ingest_read_deadline(line_idx, bytes_read)
          yield line
  except FileNotFoundError:
    return


def _digit_line_identity(s: Any) -> Any:
  """
  Return ``(t, jid, host)`` from a digit-leading line, or ``None`` if malformed.

  Accepts extra trailing tokens (monitor lines may carry more than three
    fields).

  Args:
    s (Any): S passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _digit_line_identity(None)  # doctest: +SKIP
  """
  try:
    parts = s.split()
    if len(parts) < 3:
      return None
    return (parts[0], parts[1], parts[2])
  except TypeError, ValueError, AttributeError:
    return None


def _digit_line_unix_second(s: Any) -> Any:
  """
  Return unix-second from a digit-leading line, or ``None`` if malformed.

  Args:
    s (Any): S passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _digit_line_unix_second(None)  # doctest: +SKIP
  """
  parsed = _digit_line_identity(s)
  if parsed is None:
    return None
  try:
    return int(float(parsed[0]))
  except TypeError, ValueError:
    return None


def parse_first_timestamp_line(lines: Any) -> Any:
  """
  Parse the first timestamp line.

  Args:
    lines (Any): Lines passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> parse_first_timestamp_line(None)  # doctest: +SKIP
  """
  for l in lines:
    if not l:
      continue
    try:
      s = l.lstrip()
      if not s:
        continue
      if s[0].isdigit():
        parsed = _digit_line_identity(s)
        if parsed is None:
          continue
        return parsed
    except Exception:
      pass
  return (None, None, None)


def parse_last_timestamp_line(lines: Any) -> Any:
  """
  Return last digit-leading stats line identity from an in-memory line list.

  Args:
    lines (Any): Lines passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> parse_last_timestamp_line(None)  # doctest: +SKIP
  """
  for line in reversed(lines or ()):
    if not line:
      continue
    try:
      s = line.lstrip()
      if not s:
        continue
      if s[0].isdigit():
        parsed = _digit_line_identity(s)
        if parsed is None:
          continue
        return parsed
    except Exception:
      pass
  return (None, None, None)


def parse_last_timestamp_line_streaming(
  stats_file: str,
  *,
  tail_read_bytes: int = 65536,
) -> Any:
  """
  Return last digit-leading stats line identity without a full-file scan.

  Args:
    stats_file (str): String for stats file.
    tail_read_bytes (int): Integer value for tail read bytes.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> parse_last_timestamp_line_streaming("x", 0)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    update_worker_substage,
  )

  update_worker_substage("parse:tail")
  try:
    size = os.path.getsize(stats_file)
  except OSError:
    return (None, None, None)
  if size <= 0:
    return (None, None, None)
  chunk_size = max(4096, int(tail_read_bytes))
  pieces: list[tuple[int, bytes]] = []
  try:
    with _stats_file_read_lock(stats_file), open(stats_file, "rb") as fd:
      offset = size
      while offset > 0:
        read_size = min(chunk_size, offset)
        offset -= read_size
        fd.seek(offset)
        pieces.append((offset, fd.read(read_size)))
  except FileNotFoundError:
    return (None, None, None)
  carry = b""
  for offset, raw_block in pieces:
    block = raw_block + carry
    parts = block.split(b"\n")
    if offset > 0:
      carry = parts[0]
      parts = parts[1:]
    else:
      carry = b""
    for raw in reversed(parts):
      if not raw:
        continue
      try:
        line = raw.decode("utf-8", errors="replace")
      except Exception:
        continue
      s = line.lstrip()
      if not s or not s[0].isdigit():
        continue
      parsed = _digit_line_identity(s)
      if parsed is not None:
        return parsed
  return (None, None, None)


def _timestamp_present_for_duplicate(
  itimes_set: Any,
  timestamp_present: Any,
  unix_second: Any,
) -> Any:
  """
  Internal helper to handle timestamp present for duplicate.

  Args:
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any): Timestamp present passed to this helper.
    unix_second (Any): Unix second passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _timestamp_present_for_duplicate(None, None, None)  # doctest: +SKIP
  """
  if timestamp_present is not None:
    return bool(timestamp_present(unix_second))
  return int(unix_second) in itimes_set


def find_processing_start_index(
  lines: Any,
  itimes_set: Any,
  timestamp_present: Any | None = None,
) -> Any:
  """
  Find the processing start index.

  Args:
    lines (Any): Lines passed to this helper.
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> find_processing_start_index(None, None, None)  # doctest: +SKIP
  """
  start_idx = -1
  last_idx = 0
  need_archival = True
  for i, line in enumerate(lines):
    if i and i % 1000 == 0:
      from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
        update_worker_substage,
      )

      update_worker_substage("duplicate_scan_lines")
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      unix_sec = _digit_line_unix_second(s)
      if unix_sec is None:
        continue
      if not _timestamp_present_for_duplicate(
        itimes_set, timestamp_present, unix_sec
      ):
        start_idx = last_idx
        need_archival = True
        break
      last_idx = i
  return start_idx, need_archival


def find_processing_start_index_streaming(
  stats_file: str,
  itimes_set: Any,
  *,
  timestamp_present: Any | None = None,
) -> Any:
  """
  Scan a stats file without loading it into memory.

  Args:
    stats_file (str): String for stats file.
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> find_processing_start_index_streaming("x", None, None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    update_worker_substage,
  )

  update_worker_substage("duplicate_scan_streaming")
  start_idx = -1
  last_idx = 0
  line_idx = 0
  for line in iter_stats_file_lines(stats_file):
    if line_idx and line_idx % 1000 == 0:
      update_worker_substage("duplicate_scan_streaming")
    if not line:
      line_idx += 1
      continue
    s = line.lstrip()
    if not s:
      line_idx += 1
      continue
    if s[0].isdigit():
      unix_sec = _digit_line_unix_second(s)
      if unix_sec is None:
        line_idx += 1
        continue
      if not _timestamp_present_for_duplicate(
        itimes_set, timestamp_present, unix_sec
      ):
        start_idx = last_idx
        return start_idx, True
      last_idx = line_idx
    line_idx += 1
  return start_idx, True


def parse_first_timestamp_line_streaming(stats_file: str) -> Any:
  """
  Return first digit-leading stats line identity without ``readlines()``.

  Args:
    stats_file (str): String for stats file.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> parse_first_timestamp_line_streaming("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    update_worker_substage,
  )

  update_worker_substage("parse:head")
  for line in iter_stats_file_lines(stats_file):
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      parsed = _digit_line_identity(s)
      if parsed is not None:
        return parsed
  return (None, None, None)


def _collect_tail_timestamp_lines(
  stats_file: str,
  *,
  max_lines: Any,
  tail_read_bytes: int = 65536,
) -> Any:
  """
  Collect up to ``max_lines`` digit-leading lines from the file tail (newest.

    first).

  Args:
    stats_file (str): String for stats file.
    max_lines (Any): Max lines passed to this helper.
    tail_read_bytes (int): Integer value for tail read bytes.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _collect_tail_timestamp_lines("x", None, 0)  # doctest: +SKIP
  """
  try:
    size = os.path.getsize(stats_file)
  except OSError:
    return []
  if size <= 0 or max_lines <= 0:
    return []
  chunk_size = max(4096, int(tail_read_bytes))
  pieces: list[tuple[int, bytes]] = []
  offset = size
  try:
    with _stats_file_read_lock(stats_file), open(stats_file, "rb") as fd:
      while offset > 0:
        read_size = min(chunk_size, offset)
        offset -= read_size
        fd.seek(offset)
        pieces.append((offset, fd.read(read_size)))
  except FileNotFoundError:
    return []
  carry = b""
  collected = []
  for offset, raw_block in pieces:
    if len(collected) >= max_lines:
      break
    block = raw_block + carry
    parts = block.split(b"\n")
    if offset > 0:
      carry = parts[0]
      parts = parts[1:]
    else:
      carry = b""
    for raw in reversed(parts):
      if len(collected) >= max_lines:
        break
      if not raw:
        continue
      try:
        line = raw.decode("utf-8", errors="replace")
      except Exception:
        continue
      s = line.lstrip()
      if not s or not s[0].isdigit():
        continue
      collected.append(line)
  return collected


def tail_window_timestamps_all_present_streaming(
  stats_file: str,
  itimes_set: Any,
  *,
  timestamp_present: Any | None = None,
  max_lines: Any | None = None,
) -> Any:
  """
  True when every timestamp in the tail window is already present in DB/cache.

  Args:
    stats_file (str): String for stats file.
    itimes_set (Any): Itimes set passed to this helper.
    timestamp_present (Any | None): One of ``Any``, ``None``.
    max_lines (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> tail_window_timestamps_all_present_streaming("x", None, None, None)
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    update_worker_substage,
  )

  if max_lines is None:
    max_lines = cfg.get_sync_ingest_db_complete_tail_window_lines()
  update_worker_substage("parse:tail_window")
  lines = _collect_tail_timestamp_lines(stats_file, max_lines=max_lines)
  if not lines:
    return False
  for line in lines:
    s = line.lstrip()
    if not s or not s[0].isdigit():
      continue
    unix_sec = _digit_line_unix_second(s)
    if unix_sec is None:
      continue
    if not _timestamp_present_for_duplicate(
      itimes_set, timestamp_present, unix_sec
    ):
      return False
  return True


def _shared_row_dev(dev: str, typ: str, jid: Any, shared: bool) -> str:
  """
  Stamp shared-node non-GPU rows with ``j:<jid>`` before collapse.

  ``host_mem_job`` always stamps ``j:<jid>`` (even without ``%jobs``), because
  sole full-resource samples still emit per-jid cgroup memory.

  Args:
    dev (str): Monitor device token.
    typ (str): Stats type after legacy rename.
    jid (Any): Sample-header job id.
    shared (bool): True when this sample carried ``%jobs``.

  Returns:
    str: Device token stored on the stats row.

  Examples:
    >>> _shared_row_dev("0", "cpu", "42", True)
    'j:42'
    >>> _shared_row_dev("0", "nvidia_gpu", "42", True)
    '0'
    >>> _shared_row_dev("62909", "host_mem_job", "62909", False)
    'j:62909'
  """
  if typ in _GPU_STATS_TYPES:
    return dev
  if not jid or jid in ("-", "0"):
    return dev
  if typ == _HOST_MEM_JOB_TYPE:
    if isinstance(dev, str) and dev.startswith("j:"):
      return dev[:64]
    return f"j:{jid}"[:64]
  if not shared:
    return dev
  return f"j:{jid}"[:64]


class IncrementalStatsParser:
  """
  Stateful parser for chunked/streaming stats-file ingest.

  Attributes:
    _line_index: Attribute.
    _proc_by_key: Online peak-merged host_proc rows keyed by ``(jid, host, proc)``.
    exclude_types_list: Attribute.
    insert: Attribute.
    line_ctx: Attribute.
    proc_stats: Attribute.
    schema: Attribute.
    schema_bare: Attribute.
    schema_compiled: Attribute.
    schema_fast: Attribute.
    schema_fast_bare: Attribute.
    schema_fast_compiled: Attribute.
    start_idx: Attribute.
    _stats_cols: Attribute.
    _arm_shared: True after ``%jobs`` until the next sample header.
    _sample_shared: True when the current sample's ``%jobs`` named a jid.
    marks: ``%jobs`` / ``%affinity`` dicts not yet flushed to side tables.
    _pending_marks: Marks still waiting for a digit sample header.
  """

  def __init__(
    self,
    start_idx: int = 0,
    exclude_types_list: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.

    Args:
      start_idx (int): Integer value for start idx.
      exclude_types_list (Any | None): One of ``Any``, ``None``.

    Returns:
      None

    Examples:
      >>> IncrementalStatsParser(0).start_idx
      0
    """
    self.start_idx = int(start_idx)
    self.exclude_types_list = (
      exclude_types_list if exclude_types_list is not None else exclude_types
    )
    self._line_index = 0
    self.schema = {}
    self.schema_fast = {}
    self.schema_compiled = {}
    self.schema_fast_compiled = {}
    self.schema_bare = {}
    self.schema_fast_bare = {}
    self._stats_cols = _empty_stats_columns()
    self._proc_by_key: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    self.insert = False
    self.line_ctx = {"tags": None, "tags2": None}
    self._arm_shared = False
    self._sample_shared = False
    self.marks: list[dict[str, Any]] = []
    self._pending_marks: list[dict[str, Any]] = []

  @property
  def proc_stats(self) -> list[dict[str, Any]]:
    """
    Current host_proc rows (already peak-merged by ``(jid, host, proc)``).

    Returns:
      list[dict[str, Any]]: One row per unique key (insertion order).

    Examples:
      >>> IncrementalStatsParser(0).proc_stats
      []
    """
    return list(self._proc_by_key.values())

  @proc_stats.setter
  def proc_stats(self, value: Any) -> None:
    """
    Replace or clear the online proc map (``[]`` / falsy clears).

    Args:
      value (Any): Empty clears; a sequence of row dicts rehydrates the map.

    Returns:
      None

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p.proc_stats = []
    """
    self._proc_by_key = {}
    if not value:
      return
    for row in value:
      self._merge_proc_row_online(row)

  def take_proc_stats(self) -> OnlineMergedProcRows:
    """
    Return and clear online-merged host_proc rows for a flush.

    Returns:
      OnlineMergedProcRows: Peak-merged rows; map is empty afterward.

    Examples:
      >>> IncrementalStatsParser(0).take_proc_stats()
      []
    """
    rows = OnlineMergedProcRows(self._proc_by_key.values())
    self._proc_by_key = {}
    return rows

  def take_proc_stats_columns(self) -> dict[str, list[Any]]:
    """
    Return and clear online-merged host_proc rows as a columnar SoA payload.

    Returns:
      dict[str, list[Any]]: Empty dict when no rows; else column lists.

    Examples:
      >>> IncrementalStatsParser(0).take_proc_stats_columns()
      {}
    """
    rows = list(self._proc_by_key.values())
    self._proc_by_key = {}
    return _proc_rows_to_columns(rows)

  def _merge_proc_row_online(self, row: dict[str, Any]) -> None:
    """
    Merge one host_proc row into ``_proc_by_key`` (GREATEST peaks / last-write).

    Args:
      row (dict[str, Any]): Sparse host_proc sample (owned after first insert).

    Returns:
      None

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p._merge_proc_row_online(
      ...   {"jid": "j", "host": "h", "proc": "p", "vm_peak": 1}
      ... )
    """
    key = (row.get("jid"), row.get("host"), row.get("proc"))
    if not _parse_stage_telem_on:
      existing = self._proc_by_key.get(key)
      if existing is None:
        self._proc_by_key[key] = row
      else:
        merge_proc_row_dicts(existing, row)
      return
    with _held_parse_stage("proc_merge_s"):
      existing = self._proc_by_key.get(key)
      if existing is None:
        self._proc_by_key[key] = row
      else:
        merge_proc_row_dicts(existing, row)

  def compile_injected_schema(self) -> None:
    """
    Compile ``schema`` / ``schema_fast`` after listend injects ``!`` maps.

    Returns:
      None

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p.schema = {"cpu": ["user,W=48"]}
      >>> p.schema_fast = {"cpu": ["user,W=48"]}
      >>> p.compile_injected_schema()
      >>> p.schema_compiled["cpu"]["events"][0]
      'user'
    """
    for typ, tokens in self.schema.items():
      token_list = list(tokens)
      self.schema_compiled[typ] = _compile_schema_tokens(token_list)
      self.schema_bare[typ] = _compile_schema_bare_names(token_list)
    for typ, tokens in self.schema_fast.items():
      token_list = list(tokens)
      self.schema_fast_compiled[typ] = _compile_schema_tokens(token_list)
      self.schema_fast_bare[typ] = _compile_schema_bare_names(token_list)

  def _compiled_schema_for(
    self,
    typ: str,
    *,
    fast: bool,
  ) -> dict[str, list]:
    """
    Return compiled SoA fields for ``typ``, compiling on first use.

    Args:
      typ (str): Hardware type label from the stats line.
      fast (bool): True to use ``schema_fast`` (``@fast`` samples).

    Returns:
      dict[str, list]: Compiled SoA schema, or empty arrays when the type is
        unknown.

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p.schema = {"cpu": ["user"]}
      >>> p._compiled_schema_for("cpu", fast=False)["events"][0]
      'user'
    """
    store = self.schema_fast_compiled if fast else self.schema_compiled
    bare_store = self.schema_fast_bare if fast else self.schema_bare
    compiled = store.get(typ)
    if compiled is not None:
      return compiled
    tokens = self.schema_fast.get(typ) if fast else self.schema.get(typ)
    if not tokens:
      return _empty_compiled_schema()
    token_list = list(tokens)
    compiled = _compile_schema_tokens(token_list)
    store[typ] = compiled
    bare_store[typ] = _compile_schema_bare_names(token_list)
    return compiled

  def _proc_bare_keys_for(
    self,
    typ: str,
    schema_keys: list[str],
    *,
    fast: bool,
  ) -> list[str]:
    """
    Return compiled proc bare names for ``typ``, compiling on first use.

    Args:
      typ (str): ``proc`` / ``host_proc`` type label.
      schema_keys (list[str]): Token list used when bare cache misses.
      fast (bool): True to use ``schema_fast_bare``.

    Returns:
      list[str]: Bare key names aligned with ``schema_keys``.

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p._proc_bare_keys_for("host_proc", ["vm_peak,U=kB"], fast=False)
      ['vm_peak']
    """
    store = self.schema_fast_bare if fast else self.schema_bare
    bare = store.get(typ)
    if bare is not None:
      return bare
    bare = _compile_schema_bare_names(list(schema_keys))
    store[typ] = bare
    return bare

  @property
  def stats_len(self) -> int:
    """
    Return the number of hardware event rows currently buffered.

    Returns:
      int: Length of the columnar ``time`` list.

    Examples:
      >>> IncrementalStatsParser(0).stats_len
      0
    """
    return len(self._stats_cols["time"])

  def take_stats_columns(self) -> dict[str, list]:
    """
    Detach buffered hardware columns and start a new empty buffer.

    Compiled ``!`` schema is held across flushes.

    Returns:
      dict[str, list]: Columnar stats payload for ``build_stats_dataframes``.

    Examples:
      >>> IncrementalStatsParser(0).take_stats_columns()["time"]
      []
    """
    cols = self._stats_cols
    self._stats_cols = _empty_stats_columns()
    return cols

  def take_ready_marks(self) -> list[dict[str, Any]]:
    """
    Pop marks already bound to a sample time and host.

    Returns:
      list[dict[str, Any]]: Marks safe to upsert. Unbound marks stay queued.

    Examples:
      >>> IncrementalStatsParser(0).take_ready_marks()
      []
    """
    ready: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []
    for rec in self.marks:
      if rec.get("time") is not None and rec.get("host"):
        ready.append(rec)
      else:
        keep.append(rec)
    self.marks = keep
    return ready

  def _note_percent_mark(self, line: str) -> None:
    """
    Record a ``%jobs`` or ``%affinity`` line. Other percent lines are ignored.

    Args:
      line (str): One stripped stats line starting with ``%``.

    Returns:
      None

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p._note_percent_mark("%jobs jids=9")
      >>> p._arm_shared
      True
      >>> p._note_percent_mark("%begin 9")
      >>> len(p.marks)
      1
    """
    from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
      parse_affinity_mark,
      parse_jobs_jids,
    )

    if line.startswith("%jobs"):
      jids = parse_jobs_jids(line)
      if not jids:
        return
      self._arm_shared = True
      rec: dict[str, Any] = {
        "kind": "jobs",
        "jids": jids,
        "time": None,
        "host": None,
      }
      self._queue_mark(rec)
      return
    if line.startswith("%affinity"):
      rec_a = parse_affinity_mark(line)
      if rec_a is None:
        return
      rec_a["time"] = None
      rec_a["host"] = None
      self._queue_mark(rec_a)

  def _queue_mark(self, rec: dict[str, Any]) -> None:
    """
    Store a mark, binding it when a sample header is already open.

    Args:
      rec (dict[str, Any]): Jobs or affinity record with empty time/host.

    Returns:
      None

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p._queue_mark({"kind": "jobs", "jids": ["1"], "time": None})
      >>> p.marks[0]["jids"]
      ['1']
    """
    tags = self.line_ctx.get("tags")
    if tags is not None:
      rec["time"] = tags["time"]
      rec["host"] = tags["host"]
      if rec.get("kind") == "jobs":
        jid = tags.get("jid")
        self._sample_shared = bool(jid) and jid not in ("-", "0")
    else:
      self._pending_marks.append(rec)
    self.marks.append(rec)

  def _bind_pending_marks(self, when: float, host: str, jid: str) -> None:
    """
    Attach the open sample header to marks seen before it.

    Args:
      when (float): Sample unix time from the digit header.
      host (str): Sample host token.
      jid (str): Sample-header job id, or ``-`` when idle.

    Returns:
      None

    Examples:
      >>> p = IncrementalStatsParser(0)
      >>> p._note_percent_mark("%jobs jids=4")
      >>> p._bind_pending_marks(1710000001.0, "hosta", "4")
      >>> p.marks[0]["host"]
      'hosta'
    """
    self._sample_shared = self._arm_shared and jid not in ("-", "0", "")
    self._arm_shared = False
    for rec in self._pending_marks:
      if rec.get("time") is None:
        rec["time"] = when
        rec["host"] = host
    self._pending_marks = []

  def feed_line(self, line: Any) -> None:
    """
    Feed line.

    Args:
      line (Any): Line passed to this helper.

    Returns:
      None

    Examples:
      >>> IncrementalStatsParser().feed_line(None)  # doctest: +SKIP
    """
    i = self._line_index
    self._line_index += 1
    if not line:
      return
    s = line.lstrip()
    if not s:
      return

    if s[0].isalpha() and self.insert:
      typ, dev, vals = s.split(maxsplit=2)
      vals = vals.split()
      if typ in self.exclude_types_list:
        return

      if typ in ("proc", "host_proc"):
        # device = full monitor token (name/pid/cmask/mmask); proc = name only.
        proc_name = dev.split("/", 1)[0]
        full_schema_keys = (
          self.schema.get(typ)
          or self.schema.get("host_proc")
          or self.schema.get("proc")
          or list(HOST_PROC_KEYS)
        )
        tier_marker = None
        if vals and vals[0] in _TIER_MARKERS:
          tier_marker = vals[0]
          vals = vals[1:]
        use_fast = tier_marker == "@fast"
        if use_fast:
          schema_keys = self.schema_fast.get(typ) or _fast_schema_keys(
            full_schema_keys
          )
        else:
          # ``@full`` or legacy lines without a tier marker use the full KEYS.
          schema_keys = full_schema_keys
        bare_keys = self._proc_bare_keys_for(
          typ,
          list(schema_keys),
          fast=use_fast,
        )
        tags2 = self.line_ctx["tags2"]
        row = {
          "time": tags2["time"],
          "host": tags2["host"],
          "jid": tags2["jid"],
          "proc": proc_name,
          "device": dev,
        }
        for i, bare in enumerate(bare_keys):
          if i >= len(vals):
            break
          if bare not in _HOST_PROC_KEY_SET:
            continue
          raw = vals[i]
          try:
            row[bare] = int(raw)
          except TypeError, ValueError:
            try:
              row[bare] = int(float(raw))
            except TypeError, ValueError:
              row[bare] = None
        self._merge_proc_row_online(row)
        return

      if typ not in self.schema:
        return

      tier_marker = None
      if vals and vals[0] in _TIER_MARKERS:
        tier_marker = vals[0]
        vals = vals[1:]

      if tier_marker == "@fast":
        if schema_needs_legacy_hardware_decode(typ, self.schema[typ]):
          return
        compiled = self._compiled_schema_for(typ, fast=True)
        use_legacy = False
      else:
        compiled = self._compiled_schema_for(typ, fast=False)
        use_legacy = schema_needs_legacy_hardware_decode(typ, self.schema[typ])

      tags = self.line_ctx["tags"]
      if tags is None:
        return
      out_typ = legacy_parsing.legacy_output_type(typ) if use_legacy else typ
      dev = _shared_row_dev(dev, out_typ, tags.get("jid"), self._sample_shared)
      if use_legacy:
        vals_dict = _vals_dict_from_line(
          typ, self.schema, self.schema[typ], vals, True, dev=dev
        )
        if vals_dict is None:
          return
        _append_vals_dict_columns(
          self._stats_cols,
          time=float(tags["time"]),
          host=tags["host"],
          jid=tags["jid"],
          typ=out_typ,
          dev=dev,
          vals_dict=vals_dict,
        )
        return
      _append_compiled_stats_columns(
        self._stats_cols,
        time=float(tags["time"]),
        host=tags["host"],
        jid=tags["jid"],
        typ=out_typ,
        dev=dev,
        compiled=compiled,
        vals=vals,
      )

    elif i >= self.start_idx and s[0].isdigit():
      parsed = _digit_line_identity(s)
      if parsed is None:
        return
      t, jid, host = parsed
      self.insert = True
      # Same sample-header jid as tags2; idle monitors emit "-".
      ctx = {"time": float(t), "host": host, "jid": jid}
      self.line_ctx["tags"] = ctx
      self.line_ctx["tags2"] = ctx
      self._bind_pending_marks(float(t), host, jid)
    elif s[0] == "!":
      label, events = s.split(maxsplit=1)
      typ, events = label[1:], events.split()
      self.schema[typ] = events
      self.schema_fast[typ] = _fast_schema_keys(events)
      self.schema_compiled[typ] = _compile_schema_tokens(events)
      self.schema_fast_compiled[typ] = _compile_schema_tokens(
        self.schema_fast[typ],
      )
      self.schema_bare[typ] = _compile_schema_bare_names(events)
      self.schema_fast_bare[typ] = _compile_schema_bare_names(
        self.schema_fast[typ],
      )
    elif s[0] == "%":
      self._note_percent_mark(s)

  def feed_lines(self, lines: Any) -> None:
    """
    Feed lines.

    Args:
      lines (Any): Lines passed to this helper.

    Returns:
      None

    Examples:
      >>> IncrementalStatsParser().feed_lines(None)  # doctest: +SKIP
    """
    with _held_parse_stage("feed_s"):
      for line in lines:
        self.feed_line(line)

  def finish(self) -> Any:
    """
    Finish processing and finalize state.

    Returns:
      Any: Value produced by this call (type depends on inputs).

    Examples:
      >>> IncrementalStatsParser().finish()
      ([], [])
    """
    return stats_payload_to_records(self._stats_cols), self.take_proc_stats()


def _codon_exclude_list(exclude_types_list: Any | None) -> list[str] | None:
  """
  Return the excluded event types as a sorted list for Codon.

  Mirrors ``IncrementalStatsParser``: ``None`` means the module default
  ``exclude_types``; an explicitly empty collection excludes nothing.

  Args:
    exclude_types_list (Any | None): Iterable of event-type names, or None.

  Returns:
    list[str] | None: Sorted names, or None when nothing is excluded.

  Examples:
    >>> _codon_exclude_list(frozenset({"b", "a"}))
    ['a', 'b']
    >>> _codon_exclude_list(frozenset()) is None
    True
    >>> _codon_exclude_list(None) == sorted(exclude_types)
    True
  """
  chosen = exclude_types if exclude_types_list is None else exclude_types_list
  if not chosen:
    return None
  return sorted(str(t) for t in chosen)


def parse_stats_lines(
  lines: Any,
  start_idx: Any,
  eventmaps_by_type: Any | None = None,
  exclude_types_list: Any | None = None,
) -> Any:
  """
  Parse stats and proc_stats from lines starting at start_idx.

  Legacy archives (CTL/CTR or legacy st_name) use sync_timedb_parsing_legacy.
  eventmaps_by_type is ignored (kept for API compat); detection is automatic.

  Args:
    lines (Any): Lines passed to this helper.
    start_idx (Any): Start idx passed to this helper.
    eventmaps_by_type (Any | None): One of ``Any``, ``None``.
    exclude_types_list (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> parse_stats_lines(None, None, None, None)  # doctest: +SKIP
  """
  del eventmaps_by_type
  # Fail-closed Codon pyext (optional): full parse only when extension
  # exposes parse_stats_lines_codon; otherwise CPython IncrementalStatsParser.
  if int(start_idx or 0) == 0:
    from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
      try_codon_parse_collapse,
    )

    codon_finished = try_codon_parse_collapse(
      list(lines), exclude=_codon_exclude_list(exclude_types_list)
    )
    if codon_finished is not None:
      return codon_finished
  parser = IncrementalStatsParser(start_idx, exclude_types_list)
  parser.feed_lines(lines)
  finished = parser.finish()
  from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
    flush_parser_side_marks,
  )

  flush_parser_side_marks(parser)
  return finished


def parse_stats_file_streaming(
  stats_file: str,
  *,
  start_line_idx: int = 0,
  parse_start_idx: int = 0,
  batch_size: int = STREAM_PARSE_LINE_BATCH,
  exclude_types_list: Any | None = None,
) -> Any:
  """
  Parse a large stats file in bounded batches without ``readlines()``.

  Resume offsets must feed the file prefix through the parser so ``!`` schema
  lines register; emission is gated by ``start_idx`` (same as
    ``parse_stats_lines``).
  Do not fast-forward with bare ``fd.readline()`` - that drops schema and
    silently
  discards every hardware stats line (RC-0).

  Args:
    stats_file (str): String for stats file.
    start_line_idx (int): Integer value for start line idx.
    parse_start_idx (int): Integer value for parse start idx.
    batch_size (int): Integer value for batch size.
    exclude_types_list (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> parse_stats_file_streaming("x", 0, 0, 0, None)  # doctest: +SKIP
  """
  emission_start = max(int(start_line_idx or 0), int(parse_start_idx or 0))
  parser = IncrementalStatsParser(emission_start, exclude_types_list)
  from hpcperfstats.dbload.lib.zstd_cli import (
    advise_sequential_read_for_paths,
  )

  advise_sequential_read_for_paths(stats_file)
  try:
    with open(stats_file, "rb") as fd:
      while True:
        batch = _read_stats_line_batch_decode_after_lock(
          fd,
          stats_file,
          batch_size,
        )
        if not batch:
          break
        parser.feed_lines(batch)
        del batch
  except FileNotFoundError:
    return [], []
  finished = parser.finish()
  from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
    flush_parser_side_marks,
  )

  flush_parser_side_marks(parser)
  return finished


def _stats_payload_to_frame(stats_list: Any) -> Any:
  """
  Build a hardware stats DataFrame from columns or row dicts.

  Args:
    stats_list (Any): Columnar ``dict[str, list]``, sequence of row dicts,
      or empty/``None``.

  Returns:
    Any: ``DataFrame`` of hardware stats rows (possibly empty).

  Examples:
    >>> _stats_payload_to_frame({"time": [1.0], "host": ["h"]}).iloc[0]["host"]
    'h'
    >>> _stats_payload_to_frame([]).empty
    True
  """
  if stats_list is None:
    return DataFrame()
  if isinstance(stats_list, dict):
    if stats_payload_row_count(stats_list) == 0:
      return DataFrame()
    return DataFrame(stats_list, copy=False)
  if not stats_list:
    return DataFrame()
  return DataFrame(stats_list)


def build_stats_dataframes(stats_list: Any, proc_stats_list: Any) -> Any:
  """
  Build stats and proc DataFrames from parse payloads.

  Args:
    stats_list (Any): Columnar hardware stats or a list of row dicts.
    proc_stats_list (Any): Parsed host_proc row dicts, columnar SoA dict,
      :class:`OnlineMergedProcRows`, or empty/``None``.

  Returns:
    Any: Tuple ``(stats_df, proc_stats_df)``.

  Examples:
    >>> stats_df, proc_df = build_stats_dataframes([], [])
    >>> stats_df.empty and proc_df.empty
    True
  """
  if not proc_stats_list:
    proc_stats_df = DataFrame()
  elif isinstance(proc_stats_list, dict):
    with _held_parse_stage("proc_df_s"):
      proc_stats_df = _stats_payload_to_frame(proc_stats_list)
  elif isinstance(proc_stats_list, OnlineMergedProcRows):
    with _held_parse_stage("proc_df_s"):
      cols = _proc_rows_to_columns(proc_stats_list)
      proc_stats_df = DataFrame(cols, copy=False) if cols else DataFrame()
  else:
    with _held_parse_stage("proc_merge_s"):
      merged = dedupe_proc_stats_peak_merge(proc_stats_list)
    with _held_parse_stage("proc_df_s"):
      proc_stats_df = DataFrame(merged) if merged else DataFrame()
  with _held_parse_stage("hw_df_s"):
    stats_df = _stats_payload_to_frame(stats_list)
  return stats_df, proc_stats_df


_EMPTY_DELTA_ARC_COLUMNS = [
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "unit",
  "value",
  "delta",
  "arc",
]


class DeltaCarryState:
  """
  Cross-chunk state for counter deltas and arc rates during incremental ingest.

  Attributes:
    arc: Cross-flush arc-rate carry (``\\0``-joined key -> last unix seconds).
    raw: Cross-flush counter carry (``\\0``-joined key -> ``[value, wid,
      mult, time]``).
    codon_finished_chunk: True for one flush right after
      ``try_codon_feed_batch_parse`` returns the finished-contract dict
      (stats already include ``delta``/``arc``); callers must skip
      ``compute_deltas_and_arc_chunk`` for that chunk and then clear the
      flag.
    codon_marks: Bound ``%jobs``/``%affinity`` mark dicts from the last
      Codon finished flush (empty when divert-off or marks absent).
    codon_copy_unique: When True, the current Codon frame is already
      COPY-unique normalized; write stage skips pre-COPY collapse.
  """

  __slots__ = (
    "arc",
    "codon_copy_unique",
    "codon_finished_chunk",
    "codon_marks",
    "raw",
  )

  def __init__(self) -> None:
    """
    Initialize a new instance.

    Returns:
      None

    Examples:
      >>> DeltaCarryState()  # doctest: +SKIP
    """
    self.raw = {}
    self.arc = {}
    self.codon_finished_chunk = False
    self.codon_copy_unique = False
    self.codon_marks: list[dict[str, Any]] = []


def _empty_delta_arc_frame() -> Any:
  """
  Internal helper to handle empty delta arc DataFrame.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _empty_delta_arc_frame()  # doctest: +SKIP
  """
  return DataFrame(columns=_EMPTY_DELTA_ARC_COLUMNS)


def _stats_df_has_required_delta_cols(stats_df: Any) -> Any:
  """
  Internal helper to handle stats DataFrame has required delta cols.

  Args:
    stats_df (Any): Stats df passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _stats_df_has_required_delta_cols(None)  # doctest: +SKIP
  """
  required_cols = {
    "host",
    "type",
    "dev",
    "event",
    "unit",
    "time",
    "value",
    "wid",
    "mult",
  }
  return not stats_df.empty and required_cols.issubset(stats_df.columns)


def _apply_counter_deltas(stats_df: Any, carry: Any | None = None) -> Any:
  """
  Apply counter diffs; optional cross-flush ``carry.raw`` continuity.

  Carry paths use group boundary masks after ``lexsort`` (not per-group
  ``head``/``tail`` groupby). String and object group keys become sorted
  category codes. Repeated values are coded by pointer identity, then
  remapped by value. Columns that are already categorical keep their
  codes.

  Args:
    stats_df (Any): Stats df passed to this helper.
    carry (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _apply_counter_deltas(None, None)  # doctest: +SKIP
  """
  code_cols: list[np.ndarray] = []
  for col in _COUNTER_GROUP_COLS:
    series = stats_df[col]
    if isinstance(series.dtype, pd.CategoricalDtype):
      code_cols.append(
        series.cat.codes.to_numpy(dtype=np.int64, copy=False),
      )
      continue
    # StringArray stores PyObject* in _ndarray. to_numpy() scans for NA.
    backed = getattr(series.array, "_ndarray", None)
    if (
      isinstance(backed, np.ndarray)
      and backed.dtype == object
      and backed.flags.c_contiguous
    ):
      arr = backed
    else:
      arr = np.ascontiguousarray(series.to_numpy(copy=False))
    if arr.dtype != object or arr.size == 0:
      stats_df[col] = series.astype("category")
      code_cols.append(
        stats_df[col].cat.codes.to_numpy(dtype=np.int64, copy=False),
      )
      continue
    ptrs = np.empty(arr.size, dtype=np.uintp)
    ctypes.memmove(
      ptrs.ctypes.data,
      arr.ctypes.data,
      ptrs.nbytes,
    )
    ptr_codes, uniq_ptrs = pd.factorize(ptrs, sort=False)
    first = np.full(len(uniq_ptrs), arr.size, dtype=np.intp)
    np.minimum.at(
      first,
      ptr_codes,
      np.arange(arr.size, dtype=np.intp),
    )
    val_codes, uniques = pd.factorize(arr[first], sort=True)
    codes = np.asarray(val_codes, dtype=np.int64)[ptr_codes]
    if codes.size and int(codes.min()) >= 0:
      if series.dtype == object:
        categories = list(uniques)
      else:
        categories = pd.Index(
          pd.array(list(uniques), dtype=series.dtype),
        )
      stats_df[col] = pd.Categorical.from_codes(
        codes,
        categories=categories,
      )
    code_cols.append(codes)
  sizes = tuple(int(c.max()) + 1 for c in code_cols)
  times = stats_df["time"].to_numpy(dtype=np.float64, copy=False)
  if int(np.prod(sizes, dtype=np.float64)) <= (2**63 - 1):
    gid = np.ravel_multi_index(np.vstack(code_cols), sizes)
    order = np.lexsort((times, gid))
    stats_df = stats_df.iloc[order].reset_index(drop=True)
    gid = gid[order]
    n = len(gid)
    values = stats_df["value"].to_numpy(dtype=np.float64, copy=False)
    same = np.ones(n, dtype=bool)
    if n > 1:
      same[1:] = gid[1:] == gid[:-1]
    delta = np.empty(n, dtype=np.float64)
    delta[0] = np.nan
    if n > 1:
      delta[1:] = np.where(same[1:], values[1:] - values[:-1], np.nan)
    stats_df["delta"] = delta
    is_first = np.ones(n, dtype=bool)
    if n > 1:
      is_first[1:] = gid[1:] != gid[:-1]
    is_last = np.ones(n, dtype=bool)
    if n > 1:
      is_last[:-1] = gid[:-1] != gid[1:]
  else:
    stats_df = stats_df.sort_values(by=[*_COUNTER_GROUP_COLS, "time"])
    stats_df["delta"] = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True)[
      "value"
    ].diff()
    is_first = is_last = None

  if carry is not None and carry.raw:
    if is_first is not None:
      first = stats_df.loc[is_first]
    else:
      first = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).head(1)
    if not first.empty:
      keys = list(
        zip(
          *(first[col].tolist() for col in _COUNTER_GROUP_COLS),
          strict=True,
        )
      )
      values = first["value"].to_numpy(dtype=np.float64, copy=False)
      idxs = first.index.to_numpy()
      raw = carry.raw
      no_prev = (np.nan,)
      prev_vals = np.fromiter(
        (
          (prev[0] if isinstance(prev, tuple) else prev["value"])
          for prev in (raw.get(key, no_prev) for key in keys)
        ),
        dtype=np.float64,
        count=len(keys),
      )
      hit = np.isfinite(prev_vals)
      if hit.any():
        stats_df.loc[idxs[hit], "delta"] = values[hit] - prev_vals[hit]

  values = stats_df["value"].to_numpy(dtype=np.float64, copy=False)
  wid = stats_df["wid"].to_numpy(dtype=np.float64, copy=False)
  delta = np.array(
    stats_df["delta"].to_numpy(dtype=np.float64, copy=False),
    dtype=np.float64,
    copy=True,
  )
  finite_neg = (delta < 0) & np.isfinite(delta)
  if finite_neg.any():
    modulus = np.power(2.0, wid)
    prev = values - delta
    one_wrap = (
      finite_neg
      & np.isfinite(prev)
      & (prev >= (modulus * 0.5))
      & (delta > -modulus)
    )
    delta[one_wrap] = modulus[one_wrap] + delta[one_wrap]
    delta[finite_neg & ~one_wrap] = np.nan
  mult = stats_df["mult"].to_numpy(dtype=np.float64, copy=False)
  stats_df["delta"] = delta * mult

  if carry is not None:
    if is_last is not None:
      last = stats_df.loc[is_last]
    else:
      last = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).tail(1)
    if not last.empty:
      keys = list(
        zip(
          *(last[col].tolist() for col in _COUNTER_GROUP_COLS),
          strict=True,
        )
      )
      carry.raw.update(
        zip(
          keys,
          zip(
            last["value"].to_numpy(dtype=np.float64, copy=False).tolist(),
            last["wid"].to_numpy(dtype=np.int64, copy=False).tolist(),
            last["mult"].to_numpy(dtype=np.float64, copy=False).tolist(),
            last["time"].to_numpy(dtype=np.float64, copy=False).tolist(),
            strict=True,
          ),
          strict=True,
        )
      )

  stats_df.drop(columns=["wid", "mult"], inplace=True)
  return stats_df


def _normalize_collapse_dev_column(stats_df: Any) -> Any:
  """
  Fill missing ``dev`` with ``''`` so group keys and UNIQUE semantics match.

  Args:
    stats_df (Any): Stats df passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _normalize_collapse_dev_column(None)  # doctest: +SKIP
  """
  if "dev" not in stats_df.columns:
    stats_df["dev"] = ""
    return stats_df
  dev = stats_df["dev"]
  stats_df["dev"] = dev.where(dev.notna(), "")
  return stats_df


def _partition_job_stamped_rows(stats_df: Any) -> tuple[Any, Any]:
  """
  Split rows whose ``dev`` is a ``j:`` job stamp from hardware-dev rows.

  Args:
    stats_df (Any): Frame that includes a ``dev`` column.

  Returns:
    tuple[Any, Any]: ``(plain, stamped)``. ``stamped`` is empty when no
    row uses the ``j:`` prefix, and ``plain`` is the original frame.

  Examples:
    >>> import pandas as pd
    >>> plain, stamped = _partition_job_stamped_rows(
    ...   pd.DataFrame({"dev": ["0", "j:9"]})
    ... )
    >>> list(stamped["dev"])
    ['j:9']
  """
  empty = stats_df.iloc[0:0]
  if stats_df.empty or "dev" not in getattr(stats_df, "columns", ()):
    return stats_df, empty
  mask = stats_df["dev"].str.startswith("j:", na=False)
  if not bool(mask.any()):
    return stats_df, empty
  return stats_df.loc[~mask], stats_df.loc[mask]


def _collapse_stats_with_deltas(stats_df: Any) -> Any:
  """
  Collapse multi-row samples. Hardware ``dev`` stays in the group key.

  Args:
    stats_df (Any): Stats df passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _collapse_stats_with_deltas(None)  # doctest: +SKIP
  """
  with _held_parse_stage("collapse_normalize_s"):
    stats_df = _normalize_collapse_dev_column(stats_df)
    gcols_gpu = _COLLAPSE_GROUP_COLS_WITH_DEV
    nv_df = stats_df[stats_df["type"] == "nvidia_gpu"]
    other_gpu_df = stats_df[stats_df["type"].isin({"amd_gpu", "intel_gpu"})]
    rest_df = stats_df[~stats_df["type"].isin(_GPU_STATS_TYPES)]
    ccm_power_df = rest_df.iloc[0:0]
    rest_other = rest_df
    if not rest_df.empty:
      ccm_power_mask = rest_df["type"].isin(_HOST_CPU_HW_TYPES) & rest_df[
        "event"
      ].isin(_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS)
      ccm_power_df = rest_df[ccm_power_mask]
      rest_other = rest_df[~ccm_power_mask]
  parts = []
  if not rest_other.empty:
    rest_plain, rest_job = _partition_job_stamped_rows(rest_other)
    with _held_parse_stage("collapse_host_sum_s"):
      # Multi-dev host paths always duplicate gcols (dev dropped); skip
      # identity probe and category-cast before groupby (E8 non-transfer).
      # ``j:`` stamps stay on ``gcols_gpu`` so two jobs at one second do not merge.
      if not rest_plain.empty:
        collapsed_rest = _groupby_sum_min_count(
          rest_plain,
          gcols_gpu,
          assume_duplicates=True,
        )
        parts.append(collapsed_rest)
      if not rest_job.empty:
        parts.append(
          _groupby_sum_min_count(
            rest_job,
            gcols_gpu,
            assume_duplicates=True,
          )
        )
  if not ccm_power_df.empty:
    ccm_plain, ccm_job = _partition_job_stamped_rows(ccm_power_df)
    with _held_parse_stage("collapse_ccm_s"):
      if not ccm_plain.empty:
        collapsed_ccm = _collapse_dcg_cpu_power_vectorized(ccm_plain, gcols_gpu)
        parts.append(collapsed_ccm)
      if not ccm_job.empty:
        parts.append(_collapse_dcg_cpu_power_vectorized(ccm_job, gcols_gpu))
  if not other_gpu_df.empty or not nv_df.empty:
    with _held_parse_stage("collapse_gpu_s"):
      if not other_gpu_df.empty:
        # Identity groups when each (host,dev,event,time) is unique.
        parts.append(_groupby_sum_min_count(other_gpu_df, gcols_gpu))
      if not nv_df.empty:
        parts.append(_collapse_nvidia_gpu_vectorized(nv_df, gcols_gpu))

  if not parts:
    return _empty_delta_arc_frame()
  with _held_parse_stage("collapse_concat_s"):
    collapsed = concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    del parts
    if "dev" not in collapsed.columns:
      collapsed["dev"] = ""
    else:
      # Avoid mandatory object astype when already string-like without NA.
      dev = collapsed["dev"]
      if bool(dev.isna().any()):
        collapsed["dev"] = dev.fillna("").astype(str)
      elif str(dev.dtype) != "object":
        collapsed["dev"] = dev.astype(str)
  with _held_parse_stage("collapse_sort_s"):
    return collapsed.sort_values(by=[*_ARC_GROUP_COLS, "time"])


def _apply_arc_and_finalize(stats_df: Any, carry: Any | None = None) -> Any:
  """
  Compute arc rates; optional cross-flush ``carry.arc`` continuity.

  Carry paths must stay vectorized (groupby head/tail + array extract /
  Index.get_indexer once).

  Args:
    stats_df (Any): Stats df passed to this helper.
    carry (Any | None): One of ``Any``, ``None``.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _apply_arc_and_finalize(None, None)  # doctest: +SKIP
  """
  deltat = stats_df.groupby(_ARC_GROUP_COLS, observed=True)["time"].diff()
  _dy = stats_df["delta"].to_numpy(dtype=np.float64, copy=False)
  _dt = deltat.to_numpy(dtype=np.float64, copy=False)
  _arc = np.full(len(stats_df), np.nan, dtype=np.float64)
  _ok = (_dt > 0) & np.isfinite(_dt)
  np.divide(_dy, _dt, out=_arc, where=_ok)

  if carry is not None and carry.arc:
    first = stats_df.groupby(_ARC_GROUP_COLS, observed=True).head(1)
    if not first.empty:
      positions = stats_df.index.get_indexer(first.index)
      hosts = first["host"].to_numpy()
      types = first["type"].to_numpy()
      devs = (
        first["dev"].to_numpy() if "dev" in first.columns else [""] * len(first)
      )
      events = first["event"].to_numpy()
      times = first["time"].to_numpy(dtype=np.float64, copy=False)
      deltas = first["delta"].to_numpy(dtype=np.float64, copy=False)
      for i in range(len(first)):
        pos = int(positions[i])
        if pos < 0:
          continue
        prev = carry.arc.get(
          (hosts[i], types[i], str(devs[i] or ""), events[i])
        )
        if prev is None:
          continue
        prev_time = prev["time"] if isinstance(prev, dict) else prev
        dt = float(times[i]) - float(prev_time)
        if dt > 0 and np.isfinite(deltas[i]):
          _arc[pos] = float(deltas[i]) / dt

  stats_df = stats_df.copy()
  stats_df["arc"] = _arc

  if carry is not None:
    last = stats_df.groupby(_ARC_GROUP_COLS, observed=True).tail(1)
    if not last.empty:
      hosts = last["host"].to_numpy()
      types = last["type"].to_numpy()
      devs = (
        last["dev"].to_numpy() if "dev" in last.columns else [""] * len(last)
      )
      events = last["event"].to_numpy()
      times = last["time"].to_numpy(dtype=np.float64, copy=False)
      for i in range(len(last)):
        carry.arc[(hosts[i], types[i], str(devs[i] or ""), events[i])] = float(
          times[i]
        )

  return stats_df.dropna(subset=["host", "type", "event", "time", "value"])


def _warn_nonempty_stats_collapsed_to_empty(stats_df: Any) -> None:
  """
  Loud warning when a non-empty stats frame yields zero delta/arc rows.

  Args:
    stats_df (Any): Stats df passed to this helper.

  Returns:
    None

  Examples:
    >>> _warn_nonempty_stats_collapsed_to_empty(None)  # doctest: +SKIP
  """
  if stats_df is None or getattr(stats_df, "empty", True):
    return
  cols = [str(c) for c in list(stats_df.columns)]
  warnings.warn(
    f"non-empty stats frame collapsed to empty delta/arc rows={len(stats_df)} cols={cols}",
    stacklevel=3,
  )


def compute_deltas_and_arc(stats_df: Any) -> Any:
  """
  Compute the deltas and arc.

  Args:
    stats_df (Any): Stats df passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> compute_deltas_and_arc(None)  # doctest: +SKIP
  """
  if not _stats_df_has_required_delta_cols(stats_df):
    _warn_nonempty_stats_collapsed_to_empty(stats_df)
    return _empty_delta_arc_frame()
  with _held_parse_stage("delta_s"):
    stats_df = _apply_counter_deltas(stats_df)
  stats_df = _collapse_stats_with_deltas(stats_df)
  if stats_df.empty:
    return stats_df
  with _held_parse_stage("arc_s"):
    return _apply_arc_and_finalize(stats_df)


def compute_deltas_and_arc_chunk(stats_df: Any, *, carry: Any) -> Any:
  """
  Compute deltas/arc for one incremental flush; update ``carry`` in place.

  Args:
    stats_df (Any): Stats df passed to this helper.
    carry (Any): Carry passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Raises:
    ValueError: Raised when ``compute_deltas_and_arc_chunk`` hits a
    ``ValueError`` failure path.

  Examples:
    >>> compute_deltas_and_arc_chunk(None, None)  # doctest: +SKIP
  """
  if carry is None:
    raise ValueError("carry is required for incremental delta computation")
  if not _stats_df_has_required_delta_cols(stats_df):
    _warn_nonempty_stats_collapsed_to_empty(stats_df)
    return _empty_delta_arc_frame()
  with _held_parse_stage("delta_s"):
    stats_df = _apply_counter_deltas(stats_df, carry=carry)
  stats_df = _collapse_stats_with_deltas(stats_df)
  if stats_df.empty:
    return stats_df
  with _held_parse_stage("arc_s"):
    return _apply_arc_and_finalize(stats_df, carry=carry)


def _line_starts_time_sample(line: Any, line_index: Any, start_idx: Any) -> Any:
  """
  Internal helper to handle line starts time sample.

  Args:
    line (Any): Line passed to this helper.
    line_index (Any): Line index passed to this helper.
    start_idx (Any): Start idx passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _line_starts_time_sample(None, None, None)  # doctest: +SKIP
  """
  if not line:
    return False
  stripped = line.lstrip()
  if not stripped:
    return False
  return stripped[0].isdigit() and line_index >= start_idx


def _flush_incremental_side_marks(parser: Any) -> None:
  """
  Write sample marks bound so far on the streaming parser.

  Args:
    parser (Any): Incremental stats parser holding ``%jobs`` marks.

  Returns:
    None

  Examples:
    >>> _flush_incremental_side_marks(IncrementalStatsParser(0)) is None
    True
  """
  from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
    flush_parser_side_marks,
  )

  flush_parser_side_marks(parser)


def parse_stats_file_streaming_incremental(
  stats_file: str,
  *,
  start_line_idx: int = 0,
  parse_start_idx: int = 0,
  flush_rows: Any | None = None,
  flush_max_bytes: Any | None = None,
  on_chunk: Any,
  line_batch_size: int = STREAM_PARSE_LINE_BATCH,
  exclude_types_list: Any | None = None,
  delta_carry: Any | None = None,
) -> None:
  """
  Parse a large stats file, flushing complete time samples via ``on_chunk``.

  Resume offsets must feed the file prefix through the parser so ``!`` schema
  lines register; emission is gated by ``start_idx`` (same as
  ``parse_stats_lines``). Do not fast-forward with bare ``fd.readline()`` -
  that drops schema and silently discards every hardware stats line (RC-0).

  When ``flush_max_bytes`` is set, flush when raw bytes consumed in the
  current window reach that soft max (sample-boundary overage allowed).
  Otherwise ``flush_rows`` gates on parsed stats row count (tests / legacy).

  Args:
    stats_file (str): Absolute stats file path.
    start_line_idx (int): Emission start line index.
    parse_start_idx (int): Alternate emission start (max with start_line_idx).
    flush_rows (Any | None): Row threshold when ``flush_max_bytes`` is None.
    flush_max_bytes (Any | None): Soft max raw bytes per flush window.
    on_chunk (Any): Callable ``(stats_payload, proc_payload) -> None``.
    line_batch_size (int): Decode batch size.
    exclude_types_list (Any | None): Optional excluded event types.
    delta_carry (Any | None): ``DeltaCarryState`` (or compatible object with
      ``.raw``/``.arc`` dicts) passed to ``try_codon_feed_batch_parse`` for
      cross-flush counter/arc-rate continuity on the Codon-primary path.
      Ignored on the CPython ``IncrementalStatsParser`` path, where the
      caller's own ``compute_deltas_and_arc_chunk(..., carry=...)`` call
      owns that continuity instead.

  Returns:
    None

  Examples:
    >>> parse_stats_file_streaming_incremental(
    ...   "/missing", flush_rows=1, on_chunk=lambda s, p: None
    ... )
  """
  emission_start = max(int(start_line_idx or 0), int(parse_start_idx or 0))
  # Codon-primary streaming: when divert is allowed and Codon is enabled, do
  # not dual-feed IncrementalStatsParser (that erased the parse-kernel win).
  # Codon is the sole parser for the file once codon_primary is True: a
  # None/failed divert mid-file raises instead of falling back (no silent
  # CPython recovery; see try_codon_feed_batch_parse).
  parser = IncrementalStatsParser(emission_start, exclude_types_list)
  pending_flush = False
  use_bytes = flush_max_bytes is not None
  flush_rows_n = max(1, int(flush_rows or 1))
  flush_bytes_n = max(1, int(flush_max_bytes or 1))
  can_codon_divert = emission_start == 0
  codon_primary = False
  if can_codon_divert:
    from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
      codon_ingest_parse_enabled,
    )

    codon_primary = bool(codon_ingest_parse_enabled())
  wait_telem.add("codon_primary", float(codon_primary))
  wait_telem.add("emit_start", float(emission_start))
  schema_lines: list[str] = []
  window_lines: list[str] = []
  window_sample_n = 0
  line_index = 0
  from hpcperfstats.dbload.lib.zstd_cli import (
    advise_sequential_read_for_paths,
  )

  def _flush_marks_from_lines(lines: list[str]) -> None:
    """
    Feed schema/sample/``%`` lines into a marks parser and flush side tables.

    Args:
      lines (list[str]): Schema + window lines for one flush.

    Returns:
      None: Always ``None``.

    Examples:
      >>> callable(_flush_marks_from_lines)
      True
    """
    mark_parser = IncrementalStatsParser(0, None)
    for raw in lines:
      s = raw.lstrip() if raw else ""
      if not s:
        continue
      if s[0] in "%!" or s[0].isdigit():
        mark_parser.feed_line(raw)
    _flush_incremental_side_marks(mark_parser)

  def _emit_codon_or_cpython_window() -> tuple[Any, Any] | None:
    """
    Emit one buffered window via Codon when ``codon_primary``, else CPython.

    Codon is the sole parser for the file once ``codon_primary`` is True:
    a None result (extension became unavailable mid-file) raises instead
    of silently falling back to ``IncrementalStatsParser`` (that erases
    the parse-kernel win and risks double-applying counter deltas).

    Returns:
      tuple[Any, Any] | None: Payload for ``on_chunk``, or ``None`` if empty.

    Raises:
      RuntimeError: When ``codon_primary`` is True and the Codon call
        returns None (enabled but unavailable mid-file).

    Examples:
      >>> callable(_emit_codon_or_cpython_window)
      True
    """
    nonlocal window_lines, window_sample_n
    if not window_lines and not schema_lines:
      return None
    bundled = schema_lines + window_lines
    # Codon-primary files use try_codon_parse_file_stream (early return above).
    # Codon disabled for this file: parse window with IncrementalStatsParser.
    fb = IncrementalStatsParser(0, exclude_types_list)
    fb.feed_lines(bundled)
    stats_chunk = fb.take_stats_columns()
    proc_chunk = fb.take_proc_stats_columns()
    _flush_incremental_side_marks(fb)
    window_lines = []
    window_sample_n = 0
    if (
      stats_payload_row_count(stats_chunk) == 0
      and stats_payload_row_count(proc_chunk) == 0
    ):
      return None
    return stats_chunk, proc_chunk

  if codon_primary:
    flush_b = max(1, int(flush_max_bytes or 1))
    if not use_bytes:
      flush_b = max(flush_b, flush_rows_n * 65536)
    from hpcperfstats.dbload.lib.conf_parser import (
      get_sync_codon_parse_flush_max_mib,
    )
    from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
      try_codon_parse_file_stream,
    )

    flush_b = min(flush_b, get_sync_codon_parse_flush_max_mib() << 20)
    from hpcperfstats.dbload import sync_timedb as _st

    # Tiny windows bound Codon's heap; write transactions stay CPython-sized.
    coalesce_rows = _st._ingest_write_frame_slice_rows()

    with _stats_file_read_lock(stats_file):
      try_codon_parse_file_stream(
        stats_file,
        flush_b,
        delta_carry,
        on_chunk,
        exclude=_codon_exclude_list(exclude_types_list),
        coalesce_rows=coalesce_rows,
      )
    return

  advise_sequential_read_for_paths(stats_file)
  try:
    with open(stats_file, "rb") as fd:
      window_start = int(fd.tell())
      while True:
        emit: list[tuple[Any, Any]] = []
        batch = _read_stats_line_batch_decode_after_lock(
          fd,
          stats_file,
          line_batch_size,
        )
        if not batch:
          break
        batch_end = int(fd.tell())
        with _held_parse_stage("feed_s"):
          for line in batch:
            if codon_primary:
              over_threshold = (
                (batch_end - window_start) >= flush_bytes_n
                if use_bytes
                else window_sample_n >= flush_rows_n
              )
              starts = _line_starts_time_sample(
                line, line_index, emission_start
              )
              if starts and (pending_flush or over_threshold) and window_lines:
                taken = _emit_codon_or_cpython_window()
                if taken is not None:
                  emit.append(taken)
                window_start = batch_end
                pending_flush = False
              if line.startswith("!"):
                schema_lines.append(line)
              else:
                window_lines.append(line)
              if starts:
                window_sample_n += 1
              line_index += 1
              over_after = (
                (batch_end - window_start) >= flush_bytes_n
                if use_bytes
                else window_sample_n >= flush_rows_n
              )
              if over_after:
                pending_flush = True
              continue

            # CPython IncrementalStatsParser path (Codon off / resume / excludes).
            over_threshold = (
              (batch_end - window_start) >= flush_bytes_n
              if use_bytes
              else parser.stats_len >= flush_rows_n
            )
            if _line_starts_time_sample(
              line, parser._line_index, parser.start_idx
            ) and (pending_flush or over_threshold):
              if parser.stats_len or parser._proc_by_key:
                emit.append(
                  (
                    parser.take_stats_columns(),
                    parser.take_proc_stats_columns(),
                  ),
                )
                window_start = batch_end
              pending_flush = False
            parser.feed_line(line)
            over_after = (
              (batch_end - window_start) >= flush_bytes_n
              if use_bytes
              else parser.stats_len >= flush_rows_n
            )
            if over_after:
              pending_flush = True
        for stats_chunk, proc_chunk in emit:
          on_chunk(stats_chunk, proc_chunk)
        if not codon_primary:
          _flush_incremental_side_marks(parser)
    if codon_primary:
      if window_lines:
        with _held_parse_stage("take_cols_s"):
          _final = _emit_codon_or_cpython_window()
        if _final is not None:
          on_chunk(_final[0], _final[1])
    elif parser.stats_len or parser._proc_by_key:
      with _held_parse_stage("take_cols_s"):
        _final_stats = parser.take_stats_columns()
        _final_proc = parser.take_proc_stats_columns()
      on_chunk(_final_stats, _final_proc)
      _flush_incremental_side_marks(parser)
    pending_flush = False
  except FileNotFoundError:
    return
