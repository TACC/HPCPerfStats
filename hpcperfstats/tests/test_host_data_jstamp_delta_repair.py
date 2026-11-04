"""Pure planner for the shared-stamp ``delta``/``arc`` repair (no database).

Job 79282 (hpcperfstats04, 2026-11-03): ingest diffed per-core counters
against each other on ``dev=j:<jid>`` rows, so ``delta``/``arc`` were a large
constant while ``value`` stayed the correct node sum. The repair recomputes
each series from consecutive values.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib.host_data_jstamp_delta_repair import (
  EVENT_MULTIPLIER,
  SKIP_TYPES,
  WRAP48_EVENTS,
  SeriesCarry,
  group_series,
  node_delta,
  plan_series,
)

MONITOR_SRC = Path(__file__).resolve().parents[2] / "monitor" / "src"


def _rows(values, start=0.0, step=60.0, old=(9e9, 9e9)):
  """Series rows ``(time, time_s, value, delta, arc)`` with corrupt old pairs.

  Args:
    values (list[float | None]): Node-sum values in time order.
    start (float): First Unix second.
    step (float): Seconds between samples.
    old (tuple[float, float]): Stored (corrupt) delta and arc.

  Returns:
    list[tuple]: Rows for ``plan_series``.
  """
  return [
    (i, start + i * step, v, old[0], old[1]) for i, v in enumerate(values)
  ]


def test_jid_79282_shape_is_recomputed_from_values():
  """Stored delta 1.245e8 per sample becomes the real per-minute change."""
  values = [2.2683014e8, 2.2687285e8, 2.2691421e8]
  fixes, carry = plan_series(
    "host_cpu", "user", _rows(values, old=(1.245e8, 2.11e6)), None
  )
  assert [f.time for f in fixes] == [0, 1, 2]
  # First sample of the series has no predecessor: NULL, like ingest.
  assert fixes[0].delta is None and fixes[0].arc is None
  assert fixes[1].delta == pytest.approx(values[1] - values[0])
  assert fixes[1].arc == pytest.approx((values[1] - values[0]) / 60.0)
  assert carry == SeriesCarry(120.0, values[2])


def test_unit_multiplier_scales_ib_and_block_deltas():
  """``U=4B`` / ``U=512B`` scaled delta at ingest; the stored unit lost it."""
  ib, _ = plan_series("host_ib", "port_xmit_data", _rows([0.0, 10.0]), None)
  blk, _ = plan_series("host_block", "rd_sectors", _rows([0.0, 10.0]), None)
  assert ib[1].delta == 40.0
  assert blk[1].delta == 5120.0


def test_negative_delta_is_a_wrap_only_on_48_bit_events():
  """One wrapped core on a 48-bit PMC event adds 2**48; others are gaps."""
  assert node_delta("host_cpu_hw", "instr_retired", 10.0, 5.0) == 2.0**48 - 5
  # Two cores wrapped in one interval: two multiples.
  assert node_delta("host_cpu_hw", "aperf", 2.0**49, 3.0) == 3.0
  assert node_delta("host_ib", "port_xmit_data", 10.0, 5.0) is None
  assert node_delta("host_cpu", "user", 10.0, 5.0) is None


def test_null_value_breaks_the_series_and_dt_zero_has_no_arc():
  """A NULL value gives NULL delta for it and for the next sample."""
  fixes, _ = plan_series("host_cpu", "user", _rows([1.0, None, 5.0]), None)
  assert [(f.delta, f.arc) for f in fixes] == [(None, None)] * 3
  same_time = [(0, 0.0, 1.0, 9e9, 9e9), (1, 0.0, 4.0, 9e9, 9e9)]
  fixes, _ = plan_series("host_cpu", "user", same_time, None)
  assert fixes[1].delta == 3.0 and fixes[1].arc is None


def test_carry_continues_a_series_across_slices():
  """The next slice's first row diffs against the carried last sample."""
  _fixes, carry = plan_series("host_cpu", "user", _rows([1.0, 2.0]), None)
  later = _rows([4.0], start=120.0)
  fixes, _ = plan_series("host_cpu", "user", later, carry)
  assert (fixes[0].delta, fixes[0].arc) == (2.0, pytest.approx(2.0 / 60.0))


def test_rows_already_right_are_not_rewritten():
  """Idempotence: a second pass over repaired rows plans no fixes."""
  rows = _rows([1.0, 2.0, 4.0])
  fixes, _ = plan_series("host_cpu", "user", rows, None)
  repaired = [
    (t, ts, v, f.delta, f.arc)
    for (t, ts, v, _d, _a), f in zip(rows, fixes, strict=True)
  ]
  assert plan_series("host_cpu", "user", repaired, None)[0] == []


def test_group_series_sorts_and_skips_gpu_and_job_memory():
  """GPU types keep hardware dev; host_mem_job has one device per job."""
  rows = [
    (2, 2.0, "host_cpu", "user", 5.0, None, None),
    (1, 1.0, "host_cpu", "user", 4.0, None, None),
    (1, 1.0, "nvidia_gpu", "gpu_util", 9.0, None, None),
    (1, 1.0, "host_mem_job", "mem_used", 9.0, None, None),
  ]
  out = group_series(rows)
  assert list(out) == [("host_cpu", "user")]
  assert [r[0] for r in out[("host_cpu", "user")]] == [1, 2]
  assert {"nvidia_gpu", "amd_gpu", "intel_gpu", "host_mem_job"} <= set(
    SKIP_TYPES
  )


def _monitor_schema_options():
  """``event -> option string`` from the monitor's ``X(event, "opts", ...)``.

  Returns:
    dict[str, str]: Schema options per event name.
  """
  out = {}
  pattern = re.compile(r'X\(\s*(\w+),\s*"([^"]*)"')
  for path in list(MONITOR_SRC.glob("*.c")) + list(MONITOR_SRC.glob("*.h")):
    for event, opts in pattern.findall(path.read_text(errors="replace")):
      out[event] = opts
  return out


@pytest.mark.skipif(not MONITOR_SRC.is_dir(), reason="monitor/src absent")
def test_tables_match_the_monitor_schema_declarations():
  """Drift guard: every ``W=48`` and numeric ``U=`` event is in the tables."""
  opts = _monitor_schema_options()
  assert opts, "no X(...) schema declarations found"
  w48 = {e for e, o in opts.items() if re.search(r"(^|,)W=48(,|$)", o)}
  assert w48 == set(WRAP48_EVENTS)
  numeric_units = {}
  for event, o in opts.items():
    m = re.search(r"(?:^|,)U=(\d+)[A-Za-z]", o)
    if m:
      numeric_units[event] = float(m.group(1))
  assert numeric_units == {e: m for (_t, e), m in EVENT_MULTIPLIER.items()}
