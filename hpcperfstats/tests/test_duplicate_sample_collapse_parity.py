"""A sample delivered twice in one parse batch counts once, in both engines.

hs04 2026-11-09 12:42-12:47 UTC: every ``host_data.value`` of an exclusive
job doubled on 78 of 100 hosts (``gpu_util`` 93 -> 186, ``host_cpu`` ``user``
6.894e8 -> 1.379e9, ``host_ib`` ``port_rcv_data`` 1.9e15 -> 3.8e15) while the
arc stayed right. Two identical copies of each sample sat in one collapse
batch: the copy's delta is 0, and the per-node collapse summed the values.
"""

from __future__ import annotations

import math

import pandas as pd

from hpcperfstats.tests.test_keep_dev_type_set_parity import (
  _codon_rows,
  _cpython_rows,
)

_SCHEMA = [
  "!host_cpu user,E,W=64",
  "!host_ib port_rcv_data,E,W=64,U=B",
  "!nvidia_gpu gpu_util,U=% power_usage,U=W",
]


def _sample(t: float, user0: int, user1: int, ib: int) -> list[str]:
  return [
    f"{t} 86943 hostA",
    f"host_cpu 0 @full {user0}",
    f"host_cpu 1 @full {user1}",
    f"host_ib mlx5_0:1 @full {ib}",
    "nvidia_gpu 0 @full 93 590",
  ]


def resent_sample_lines() -> list[str]:
  """Three samples; the middle one arrives twice (a whole resent copy)."""
  mid = _sample(1010.0, 1100, 2200, 50000)
  return (
    _SCHEMA
    + _sample(1000.0, 1000, 2000, 10000)
    + mid
    + mid
    + _sample(1020.0, 1200, 2400, 90000)
  )


def _one(
  out: pd.DataFrame, typ: str, event: str, dev: str = ""
) -> pd.DataFrame:
  sel = (out["type"] == typ) & (out["event"] == event) & (out["dev"] == dev)
  return out.loc[sel].sort_values("time").reset_index(drop=True)


def _check(out: pd.DataFrame) -> None:
  cpu = _one(out, "host_cpu", "user")
  # Two CPUs still sum per node; the resent copy does not add a third.
  assert cpu["value"].tolist() == [3000.0, 3300.0, 3600.0]
  assert cpu["delta"].tolist()[1:] == [300.0, 300.0]
  assert cpu["arc"].tolist()[1:] == [30.0, 30.0]
  ib = _one(out, "host_ib", "port_rcv_data")
  assert ib["value"].tolist() == [10000.0, 50000.0, 90000.0]
  assert ib["delta"].tolist()[1:] == [40000.0, 40000.0]
  assert ib["arc"].tolist()[1:] == [4000.0, 4000.0]
  for event, want in (("gpu_util", 93.0), ("power_usage", 590.0)):
    gpu = _one(out, "nvidia_gpu", event, "0")
    assert gpu["value"].tolist() == [want] * 3, event


def test_resent_sample_counts_once_cpython() -> None:
  _check(_cpython_rows(resent_sample_lines()))


def test_resent_sample_counts_once_codon() -> None:
  _check(_codon_rows(resent_sample_lines()))


def test_resent_sample_codon_matches_cpython() -> None:
  lines = resent_sample_lines()
  keys = ["type", "dev", "event", "time"]
  p = _cpython_rows(lines).sort_values(keys).reset_index(drop=True)
  c = _codon_rows(lines).sort_values(keys).reset_index(drop=True)
  assert p[keys].astype(str).equals(c[keys].astype(str))
  for col in ("value", "delta", "arc"):
    for got, want in zip(c[col], p[col], strict=True):
      assert (math.isnan(got) and math.isnan(want)) or math.isclose(got, want)


def test_distinct_readings_at_one_key_are_not_dropped() -> None:
  """Only identical rows are copies; a different value at one key still sums."""
  lines = [
    _SCHEMA[0],
    "1000.0 1 hostA",
    "host_cpu 0 @full 10",
    "host_cpu 0 @full 15",
  ]
  for out in (_cpython_rows(lines), _codon_rows(lines)):
    assert _one(out, "host_cpu", "user")["value"].tolist() == [25.0]
