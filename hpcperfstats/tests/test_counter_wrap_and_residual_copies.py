"""Reset deltas stay NaN; residual host series do not add j: copies."""

from __future__ import annotations

import pandas as pd

from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  collapse_residual_sample_copies,
)
from hpcperfstats.analysis.metrics.lib.metrics import Metrics
from hpcperfstats.dbload.lib.sync_timedb_parsing import _apply_counter_deltas


def _counter_frame(values: list[float], wid: int = 64) -> pd.DataFrame:
  rows = []
  for idx, value in enumerate(values):
    rows.append(
      {
        "host": "h",
        "type": "nfs",
        "dev": "",
        "event": "READ_ops",
        "unit": "#",
        "wid": wid,
        "mult": 1.0,
        "time": float(idx + 1),
        "value": value,
      }
    )
  return pd.DataFrame(rows)


def test_small_negative_delta_is_nan_not_full_width_wrap():
  """A gauge dip must not become 2**64."""
  out = _apply_counter_deltas(_counter_frame([100.0, 40.0]))
  assert pd.isna(out.iloc[1]["delta"])


def test_one_modulus_wrap_from_top_half_still_corrects():
  """8-bit counter 250 -> 10 is one modulus (16), not a reset."""
  out = _apply_counter_deltas(_counter_frame([250.0, 10.0], wid=8))
  assert abs(float(out.iloc[1]["delta"]) - 16.0) < 1e-9


def test_residual_j_copies_are_not_summed():
  """Two co-tenant stamps of one sample count once."""
  rows = collapse_residual_sample_copies(
    [
      {"host": "h", "time": 1, "event": "rd", "dev": "j:1", "arc": 5.0},
      {"host": "h", "time": 1, "event": "rd", "dev": "j:2", "arc": 5.0},
      {"host": "h", "time": 1, "event": "rd", "dev": "sda", "arc": 3.0},
      {"host": "h", "time": 1, "event": "rd", "dev": "sdb", "arc": 4.0},
    ],
    "arc",
  )
  assert rows == [
    {"host": "h", "time": 1, "event": "rd", "arc": 7.0},
  ]


def test_residual_poison_sample_is_dropped():
  """A 2**64-scale rate does not survive the ceiling."""
  rows = collapse_residual_sample_copies(
    [
      {"host": "h", "time": 1, "event": "rd", "dev": "", "arc": 1.0e19},
      {"host": "h", "time": 1, "event": "rd", "dev": "", "arc": 12.0},
    ],
    "arc",
  )
  assert rows == [{"host": "h", "time": 1, "event": "rd", "arc": 12.0}]


def test_host_wide_kernel_sums_hardware_devs_and_maxes_job_stamp_copies():
  """The residual collapse sums hardware devs and maxes ``j:`` copies."""
  import numpy as np

  from hpcperfstats.analysis.metrics.lib.columnar.segments import (
    collapse_residual_copies,
  )

  keys = np.array([1, 1, 2, 2, 3, 3])
  dev_id = np.array([1, 2, -1, -2, 1, -1])
  values = np.array([1.0, 2.0, 5.0, 7.0, 4.0, 9.0])
  k, v = collapse_residual_copies(keys, dev_id, values, np.ones(6, dtype=bool))
  # key 1: hardware SUM; key 2: only j: copies -> MAX; key 3: hardware wins.
  assert k.tolist() == [1, 2, 3]
  assert v.tolist() == [3.0, 7.0, 4.0]


def test_avg_blockbw_conv_is_gib_per_second():
  """Sector deltas are already bytes via U=512B; GB/s divides by 1024**3."""
  conv = Metrics().simple_metrics_list["avg_blockbw"]["conv"]
  assert conv == 1.0 / (1024**3)
