"""Columnar Codon reductions versus the CPython collapse helpers."""

from __future__ import annotations

import importlib.util
import inspect
import math
from pathlib import Path

import numpy as np
import pandas as pd

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "feed_collapse.py"
)
PYEXT = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "pyext_ft_init.c"
)


def _load():
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_columnar_under_test", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_plain_host_sum_is_reduceat_not_discarded_copy() -> None:
  """The discarded ``np.add.reduce`` copy is gone; multi-dev sum matches."""
  mod = _load()
  src = inspect.getsource(mod.collapse_plain_host_sum)
  assert "key_to_pos" not in src
  assert "_ = float(np.add.reduce" not in src
  helper = inspect.getsource(mod._reduceat_sum_min1)
  assert "np.add.reduce(" in helper
  hosts = ["h", "h", "h"]
  types = ["cpu", "cpu", "cpu"]
  events = ["user", "user", "user"]
  units = ["#", "#", "#"]
  times = [1.0, 1.0, 2.0]
  values = [10.0, 5.0, 7.0]
  _oh, _ot, _oe, _ou, ot, sums = mod.collapse_plain_host_sum(
    hosts, types, events, units, times, values, ["nvidia_gpu"]
  )
  paired = sorted(zip(ot, sums, strict=True))
  assert paired[0][0] == 1.0
  assert math.isclose(paired[0][1], 15.0)
  assert paired[1][0] == 2.0
  assert math.isclose(paired[1][1], 7.0)

  from hpcperfstats.dbload.lib.sync_timedb_parsing import _groupby_sum_min_count

  frame = pd.DataFrame(
    {
      "host": hosts,
      "type": types,
      "event": events,
      "unit": units,
      "time": times,
      "value": values,
      "delta": [1.0, 2.0, 3.0],
    }
  )
  grouped = _groupby_sum_min_count(
    frame,
    ["host", "type", "event", "unit", "time"],
    assume_duplicates=True,
  )
  got = {
    float(t): float(v)
    for t, v in zip(grouped["time"], grouped["value"], strict=True)
  }
  assert math.isclose(got[1.0], 15.0)
  assert math.isclose(got[2.0], 7.0)


def test_j_stamp_devs_stay_distinct() -> None:
  mod = _load()
  times = [5.0, 5.0]
  hosts = ["h", "h"]
  jids = ["1", "2"]
  types = ["cpu", "cpu"]
  devs = ["j:1", "j:2"]
  events = ["user", "user"]
  units = ["#", "#"]
  values = [3.0, 4.0]
  deltas = [1.0, 1.0]
  out = mod.collapse_full_cols(
    times, hosts, jids, types, devs, events, units, values, deltas
  )
  assert sorted(out[4]) == ["j:1", "j:2"]
  assert math.isclose(sum(out[7]), 7.0)


def test_nvidia_or_matches_bitwise_or_reduce() -> None:
  mod = _load()
  values = [1.0, 2.0, 4.0]
  got = mod._or_reduceat(values)
  want = float(np.bitwise_or.reduce(np.array(values, dtype=np.uint64)))
  assert math.isclose(got, want)
  assert math.isclose(got, 7.0)


def test_dcgm_cluster_mean_matches_cpython_helper() -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _cluster_mean_sum_sorted,
  )

  mod = _load()
  blank = float(2**47)
  values = [blank, 1.0, 1.2, 5.0]
  got = mod._cluster_mean_reduceat(values, 1.0)
  want = float(_cluster_mean_sum_sorted(np.array(values), 1.0))
  assert math.isclose(got, want)


def test_fill_reuse_bank_end_mark_hides_previous_tail() -> None:
  mod = _load()
  bank = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
  mark = mod.fill_reuse_bank(bank, [11, 12, 13])
  assert mark == 3
  assert bank[:mark] == [11, 12, 13]
  assert bank[mark] == 4
  mark = mod.fill_reuse_bank(bank, list(range(10)))
  assert mark == 10
  assert bank[:mark] == list(range(10))


def test_copy_success_path_has_no_per_cell_exports() -> None:
  text = PYEXT.read_text()
  assert "pss_export_f64_c" in text
  assert "pss_pack_str_col_c" in text
  local = text.split("append_host_copy_row_local", 1)[1].split(
    "\nstatic int\n", 1
  )[0]
  assert "pss_" not in local
  assert "psp_" not in local
