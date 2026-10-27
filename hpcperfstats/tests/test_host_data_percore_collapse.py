"""Planner for the per-core host_data repair (no database)."""

from __future__ import annotations

import math

import pandas as pd

from hpcperfstats.dbload.lib.host_data_percore_collapse import (
  OUTPUT_COLS,
  percore_mask,
  plan_collapse,
)

T0 = pd.Timestamp("2026-10-24 12:00:00", tz="UTC")
T1 = T0 + pd.Timedelta(seconds=10)


def _row(dev, time, value, delta, arc, *, typ="cpu", event="user", jid="77"):
  return {
    "host": "h1",
    "jid": jid,
    "type": typ,
    "dev": dev,
    "event": event,
    "unit": "cs",
    "time": time,
    "value": value,
    "delta": delta,
    "arc": arc,
  }


def _context(*times, typ="cpu", event="user"):
  return pd.DataFrame(
    [{"host": "h1", "type": typ, "event": event, "time": t} for t in times]
  )


def test_percore_mask_excludes_gpu_stamped_and_whole_node_rows():
  frame = pd.DataFrame(
    {
      "type": ["cpu", "cpu", "cpu", "cpu", "nvidia_gpu", "amd_gpu"],
      "dev": ["0", "", None, "j:9", "0", "1"],
    }
  )
  assert percore_mask(frame).tolist() == [
    True,
    False,
    False,
    False,
    False,
    False,
  ]


def test_cores_sum_to_one_empty_dev_row_with_arc_from_dt():
  rows = [
    _row("0", T1, 1.0, 1.0, 1.0),
    _row("1", T1, 4.0, 2.0, 1.0),
  ]
  plan = plan_collapse(pd.DataFrame(rows), [], _context(T0, T1))
  assert list(plan.new_rows.columns) == OUTPUT_COLS
  assert len(plan.new_rows) == 1
  out = plan.new_rows.iloc[0]
  assert out["dev"] == ""
  assert out["value"] == 5.0
  assert out["delta"] == 3.0
  # arc = summed delta / dt (3 / 10), not the sum of per-core arcs (2.0).
  assert math.isclose(out["arc"], 0.3)
  assert out["jid"] == "77"
  assert (plan.percore_rows, plan.groups, plan.kept_existing) == (2, 1, 0)


def test_first_sample_without_predecessor_falls_back_to_summed_arc():
  rows = [
    _row("0", T0, 1.0, 1.0, 1.5),
    _row("1", T0, 4.0, 2.0, 0.5),
  ]
  plan = plan_collapse(pd.DataFrame(rows), [], _context(T0))
  assert plan.new_rows.iloc[0]["arc"] == 2.0


def test_existing_whole_node_row_is_kept_and_not_replanned():
  rows = [
    _row("0", T0, 1.0, 1.0, 1.0),
    _row("1", T0, 4.0, 2.0, 1.0),
    _row("0", T1, 2.0, 1.0, 1.0),
    _row("1", T1, 6.0, 2.0, 1.0),
  ]
  existing = [("h1", "cpu", "user", T0)]
  plan = plan_collapse(pd.DataFrame(rows), existing, _context(T0, T1))
  assert plan.groups == 2
  assert plan.kept_existing == 1
  assert plan.new_rows["time"].tolist() == [T1]
  assert plan.new_rows.iloc[0]["value"] == 8.0


def test_gpu_stamped_and_empty_dev_rows_are_never_collapsed():
  rows = [
    _row("0", T0, 5.0, 1.0, 1.0, typ="nvidia_gpu", event="gpu_util"),
    _row("j:9", T0, 3.0, 1.0, 1.0),
    _row("", T0, 7.0, 1.0, 1.0),
  ]
  plan = plan_collapse(pd.DataFrame(rows), [], _context(T0))
  assert plan.new_rows.empty
  assert plan.percore_rows == 0


def test_dcgm_cpu_power_uses_cluster_mean_sum_like_ingest():
  rows = []
  for dev, watts in enumerate([100.0, 100.0, 100.0, 80.0, 80.0, 80.0]):
    rows.append(
      _row(
        str(dev),
        T0,
        watts,
        float(dev + 1),
        0.0,
        typ="cpu_counter_metrics",
        event="DCGM_CPU_POWER_UTIL_W",
      )
    )
  plan = plan_collapse(
    pd.DataFrame(rows),
    [],
    _context(T0, typ="cpu_counter_metrics", event="DCGM_CPU_POWER_UTIL_W"),
  )
  assert len(plan.new_rows) == 1
  assert abs(plan.new_rows.iloc[0]["value"] - 180.0) < 1e-6


def test_insert_sql_columns_follow_the_record_order():
  """Regression: arc/delta were swapped when the two column lists diverged."""
  from hpcperfstats.site.lib.machine.management.commands import (
    collapse_percore_host_data as cmd,
  )

  listed = cmd._INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
  assert [c.strip() for c in listed.split(",")] == cmd._COLUMNS
  assert cmd._INSERT_SQL.count("%s") == len(cmd._COLUMNS)


def test_insert_records_put_value_delta_and_arc_in_their_own_slots():
  from hpcperfstats.site.lib.machine.management.commands import (
    collapse_percore_host_data as cmd,
  )

  rows = [_row("0", T1, 1.0, 1.0, 1.0), _row("1", T1, 4.0, 2.0, 1.0)]
  plan = plan_collapse(pd.DataFrame(rows), [], _context(T0, T1))
  record = dict(zip(cmd._COLUMNS, cmd._records(plan.new_rows)[0], strict=True))
  assert record["value"] == 5.0
  assert record["delta"] == 3.0
  assert math.isclose(record["arc"], 0.3)
  assert record["dev"] == ""


def test_planned_rows_match_what_ingest_now_writes():
  """Old per-core rows plan to exactly the rows compute_deltas_and_arc emits."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _apply_arc_and_finalize,
    _apply_counter_deltas,
    compute_deltas_and_arc,
  )

  def sample(dev, time, value):
    return {
      "host": "h1",
      "type": "cpu",
      "dev": dev,
      "event": "user",
      "unit": "cs",
      "time": time,
      "value": value,
      "wid": 48,
      "mult": 1,
      "jid": "77",
    }

  raw = [
    sample("0", 100.0, 10.0),
    sample("1", 100.0, 20.0),
    sample("0", 110.0, 40.0),
    sample("1", 110.0, 60.0),
  ]
  whole_node = compute_deltas_and_arc(pd.DataFrame(raw))
  want = whole_node.loc[whole_node["time"] == 110.0].iloc[0]

  # What the per-core era stored: delta and arc per (host, type, dev, event).
  stored = _apply_arc_and_finalize(_apply_counter_deltas(pd.DataFrame(raw)))
  stored = stored.loc[stored["time"] == 110.0].copy()
  stored["time"] = pd.Timestamp(110.0, unit="s", tz="UTC")
  ctx_times = [
    pd.Timestamp(100.0, unit="s", tz="UTC"),
    pd.Timestamp(110.0, unit="s", tz="UTC"),
  ]
  plan = plan_collapse(stored, [], _context(*ctx_times))
  got = plan.new_rows.iloc[0]
  assert got["dev"] == ""
  assert math.isclose(got["value"], want["value"])
  assert math.isclose(got["delta"], want["delta"])
  assert math.isclose(got["arc"], want["arc"])
