"""Plan the one-off repair of ``delta`` / ``arc`` on shared-stamped rows.

Until the per-device delta fix (2026-11-03), ingest replaced a non-GPU row's
hardware ``dev`` with ``j:<jid>`` before computing counter deltas, so every
device of a type shared one delta key and was diffed against its neighbour
(job 79282: IB 1.9 TB/s, NFS 90 PB). ``value`` is still the true node sum,
so this module recomputes each ``j:<jid>`` series from consecutive values:
``delta = (value - previous value) * multiplier`` and ``arc = delta / dt``.
It is the pure (no database) step of ``manage.py repair_jstamp_deltas``.

Limits: ``value`` is stored as PostgreSQL ``real``, so a per-sample delta is
quantized to the float's spacing at the counter's size. Sums of deltas still
telescope to ``last - first`` (totals and means are right); peaks of slow,
large counters are coarse. A negative node delta is a counter wrap on 48-bit
events (corrected by whole multiples of 2**48, one per wrapped device) and a
gap (``None``) on every other width, like ingest's gauge-dip rule.

Attributes:
  EVENT_MULTIPLIER: ``(type, event) -> U=`` multiplier the parser applied to
    ``delta`` (the stored ``unit`` keeps only the letters, ``4B`` -> ``B``).
  WRAP48_EVENTS: Events the monitor declares ``W=48``.
  SKIP_TYPES: Types whose shared-stamped rows were never merged across
    devices (GPU types keep hardware ``dev``; ``host_mem_job`` has one
    device per job).
  _WRAP48: ``2**48`` as a float.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

EVENT_MULTIPLIER: dict[tuple[str, str], float] = {
  ("host_ib", "port_rcv_data"): 4.0,
  ("host_ib", "port_xmit_data"): 4.0,
  ("host_ib", "sw_rx_bytes"): 4.0,
  ("host_ib", "sw_tx_bytes"): 4.0,
  ("host_block", "rd_sectors"): 512.0,
  ("host_block", "wr_sectors"): 512.0,
}

WRAP48_EVENTS: frozenset[str] = frozenset(
  {
    "aperf",
    "arm_int16_ops",
    "arm_int8_ops",
    "cycles_unhalted_core",
    "cycles_unhalted_ref",
    "dram_act_count",
    "dram_cas_reads",
    "dram_cas_writes",
    "dram_chan0_bytes",
    "dram_chan1_bytes",
    "dram_chan2_bytes",
    "dram_chan3_bytes",
    "dram_pre_count_miss",
    "fp_arith_inst_retired_128b_packed_double",
    "fp_arith_inst_retired_128b_packed_single",
    "fp_arith_inst_retired_256b_packed_double",
    "fp_arith_inst_retired_256b_packed_single",
    "fp_arith_inst_retired_512b_packed_double",
    "fp_arith_inst_retired_512b_packed_single",
    "fp_arith_inst_retired_scalar_double",
    "fp_arith_inst_retired_scalar_single",
    "hbm_cas_reads",
    "hbm_cas_writes",
    "instr_retired",
    "instr_retired_any",
    "l1d_replacement",
    "ls_dispatch",
    "mem_load_uops_retired_l1_hit",
    "mem_load_uops_retired_l2_hit",
    "mem_load_uops_retired_llc_hit",
    "mperf",
    "retired_branch_instr",
    "retired_instructions",
    "retired_misp_branch_instr",
  }
)

SKIP_TYPES: tuple[str, ...] = (
  "amd_gpu",
  "host_mem_job",
  "intel_gpu",
  "nvidia_gpu",
)

_WRAP48 = float(2**48)


class SeriesCarry(NamedTuple):
  """Last sample of one series, carried across time slices.

  Subclasses ``typing.NamedTuple``, the standard immutable record base; this
  class only names the two fields.

  Attributes:
    time_s: Unix seconds of the last sample.
    value: Its ``value`` (``None`` when NULL).
  """

  time_s: float
  value: float | None


class RowFix(NamedTuple):
  """New ``delta`` / ``arc`` for one row whose stored pair is wrong.

  Subclasses ``typing.NamedTuple``, the standard immutable record base; this
  class only names the four fields.

  Attributes:
    time: The row's ``time`` (the original object, for the UPDATE key).
    event: The row's ``event``.
    delta: Recomputed delta, or ``None``.
    arc: Recomputed arc, or ``None``.
  """

  time: Any
  event: str
  delta: float | None
  arc: float | None


def node_delta(
  typ: str, event: str, prev: float | None, value: float | None
) -> float | None:
  """Scaled counter delta between two node-sum values of one series.

  Args:
    typ (str): ``host_data.type``.
    event (str): ``host_data.event``.
    prev (float | None): Previous ``value`` (``None``: no predecessor).
    value (float | None): This ``value``.

  Returns:
    float | None: ``(value - prev) * multiplier``. A negative difference is
    a 48-bit wrap for ``WRAP48_EVENTS`` (whole multiples of ``2**48`` are
    added) and ``None`` otherwise; ``None`` when either value is missing.

  Examples:
    >>> node_delta("host_ib", "port_xmit_data", 10.0, 15.0)
    20.0
    >>> node_delta("host_cpu", "user", 10.0, 7.0) is None
    True
    >>> node_delta("host_cpu_hw", "aperf", 2.0**48 - 5, 3.0)
    8.0
  """
  if prev is None or value is None:
    return None
  if not (math.isfinite(prev) and math.isfinite(value)):
    return None
  diff = value - prev
  if diff < 0:
    if event not in WRAP48_EVENTS:
      return None
    diff += math.ceil(-diff / _WRAP48) * _WRAP48
  return diff * EVENT_MULTIPLIER.get((typ, event), 1.0)


def _as_real(x: float | None) -> float | None:
  """Round ``x`` the way PostgreSQL ``real`` stores it (NaN is NULL).

  Args:
    x (float | None): Value.

  Returns:
    float | None: float32-rounded value, or ``None`` for NULL / NaN.

  Examples:
    >>> _as_real(1.0000000001)
    1.0
    >>> _as_real(float("nan")) is None
    True
  """
  if x is None or math.isnan(float(x)):
    return None
  return struct.unpack("f", struct.pack("f", float(x)))[0]


def plan_series(
  typ: str,
  event: str,
  rows: Sequence[tuple[Any, float, float | None, float | None, float | None]],
  carry: SeriesCarry | None,
) -> tuple[list[RowFix], SeriesCarry | None]:
  """Recompute ``delta`` / ``arc`` for one series slice, in time order.

  Args:
    typ (str): ``host_data.type``.
    event (str): ``host_data.event``.
    rows (Sequence[tuple[Any, float, float | None, float | None, float |
      None]]): ``(time, time_s, value, delta, arc)`` sorted by time;
      ``time_s`` is Unix seconds.
    carry (SeriesCarry | None): Last sample of the previous slice, or
      ``None`` at the series start (its first row gets ``None``/``None``).

  Returns:
    tuple[list[RowFix], SeriesCarry | None]: Fixes for rows whose stored
    pair differs (as ``real``) from the recomputed one, and the carry for the
    next slice.

  Examples:
    >>> fixes, c = plan_series(
    ...   "host_cpu",
    ...   "user",
    ...   [(1, 60.0, 100.0, 9e9, 9e9), (2, 120.0, 400.0, 9e9, 9e9)],
    ...   SeriesCarry(0.0, 40.0),
    ... )
    >>> [(f.delta, f.arc) for f in fixes], c
    ([(60.0, 1.0), (300.0, 5.0)], SeriesCarry(time_s=120.0, value=400.0))
  """
  fixes: list[RowFix] = []
  for time, time_s, value, old_delta, old_arc in rows:
    delta = None
    arc = None
    if carry is not None:
      delta = node_delta(typ, event, carry.value, value)
      dt = time_s - carry.time_s
      if delta is not None and dt > 0:
        arc = delta / dt
    if (_as_real(old_delta), _as_real(old_arc)) != (
      _as_real(delta),
      _as_real(arc),
    ):
      fixes.append(RowFix(time, event, delta, arc))
    carry = SeriesCarry(time_s, value)
  return fixes, carry


def group_series(
  rows: Iterable[tuple[Any, ...]],
) -> dict[tuple[str, str], list[tuple[Any, ...]]]:
  """Split ``(time, time_s, type, event, value, delta, arc)`` rows by series.

  Args:
    rows (Iterable[tuple[Any, ...]]): Rows of one ``(host, jid)`` stamp in
      any order.

  Returns:
    dict[tuple[str, str], list[tuple[Any, ...]]]: ``(type, event)`` ->
    ``(time, time_s, value, delta, arc)`` sorted by ``time_s``; rows of
    ``SKIP_TYPES`` are dropped.

  Examples:
    >>> group_series(
    ...   [
    ...     (2, 2.0, "cpu", "user", 5.0, None, None),
    ...     (1, 1.0, "cpu", "user", 4.0, None, None),
    ...     (1, 1.0, "nvidia_gpu", "gpu_util", 9.0, None, None),
    ...   ]
    ... )
    {('cpu', 'user'): [(1, 1.0, 4.0, None, None), (2, 2.0, 5.0, None, None)]}
  """
  out: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
  for time, time_s, typ, event, value, delta, arc in rows:
    if typ in SKIP_TYPES:
      continue
    out.setdefault((typ, event), []).append((time, time_s, value, delta, arc))
  for series in out.values():
    series.sort(key=lambda r: r[1])
  return out
