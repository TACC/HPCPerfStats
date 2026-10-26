"""Vectorized host COPY encoding must match the per-cell encoder byte for byte."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from hpcperfstats.dbload.lib import sync_timedb_host_data_insert as hi


def _legacy(frame: pd.DataFrame) -> bytes:
  """Encode with the original per-cell path (reference implementation)."""
  n = len(frame)
  columns = {
    c: frame[c].tolist() if c in frame.columns else [None] * n
    for c in hi.HOST_DATA_COPY_COLUMNS
  }
  return hi._host_columns_to_copy_bytes(columns)


def _frame(n: int, seed: int = 7, tzaware: bool = False) -> pd.DataFrame:
  rng = np.random.default_rng(seed)
  t = (
    1_792_900_000
    + rng.integers(0, 86400, n)
    + rng.integers(0, 1_000_000, n) / 1e6
  )
  dev = rng.choice(["", "0", "1", "nan", " gpu0 ", None, "NaN"], n).astype(
    object
  )
  dev[rng.random(n) < 0.05] = np.nan
  val = rng.normal(0, 1e6, n)
  val[rng.random(n) < 0.05] = np.nan
  val[rng.random(n) < 0.01] = np.inf
  val[rng.random(n) < 0.01] = -0.0
  val[rng.random(n) < 0.02] = 1e-12
  val[rng.random(n) < 0.02] = 1e16
  delta = rng.normal(0, 10, n)
  delta[rng.random(n) < 0.1] = np.nan
  arc = rng.random(n) * 100
  arc[rng.random(n) < 0.1] = np.nan
  return pd.DataFrame(
    {
      "time": pd.to_datetime(t, unit="s", utc=True) if tzaware else t,
      "host": rng.choice(
        ["c001-001", "c001-002", "gpu\tweird", "back\\slash", "nl\nhost"], n
      ),
      "jid": rng.choice(["-", "123", "456", None], n).astype(object),
      "type": rng.choice(["cpu", "mem", "nvidia_gpu"], n),
      "dev": dev,
      "event": rng.choice(["user", "sys", "a\tb", "x"], n),
      "unit": rng.choice(["cs", "B", "", "KB"], n),
      "value": val,
      "delta": delta,
      "arc": arc,
    }
  )


@pytest.mark.parametrize("tzaware", [False, True])
def test_vectorized_matches_per_cell_encoder_byte_for_byte(tzaware):
  frame = _frame(5000, tzaware=tzaware)
  fast = hi._host_frame_to_copy_bytes_vectorized(frame)
  assert fast is not None
  assert fast == _legacy(frame)


def test_host_frame_to_copy_bytes_uses_vectorized_result():
  frame = _frame(500)
  assert hi._host_frame_to_copy_bytes(frame) == _legacy(frame)


def test_empty_frame_encodes_to_empty_bytes():
  assert hi._host_frame_to_copy_bytes_vectorized(pd.DataFrame()) == b""
  assert hi._host_frame_to_copy_bytes(pd.DataFrame()) == b""


def test_missing_time_falls_back_and_still_matches_legacy():
  frame = _frame(50)
  frame.loc[3, "time"] = np.nan
  assert hi._host_frame_to_copy_bytes_vectorized(frame) is None
  assert hi._host_frame_to_copy_bytes(frame) == _legacy(frame)


def test_non_string_text_column_falls_back_and_still_matches_legacy():
  frame = _frame(50)
  frame["host"] = list(range(50))
  assert hi._host_frame_to_copy_bytes_vectorized(frame) is None
  assert hi._host_frame_to_copy_bytes(frame) == _legacy(frame)


def test_absent_column_falls_back_and_still_matches_legacy():
  frame = _frame(50).drop(columns=["unit"])
  assert hi._host_frame_to_copy_bytes_vectorized(frame) is None
  assert hi._host_frame_to_copy_bytes(frame) == _legacy(frame)


def test_special_characters_are_escaped_like_the_legacy_encoder():
  frame = _frame(10)
  frame.loc[0, "host"] = "a\tb\\c\nd\re"
  out = hi._host_frame_to_copy_bytes(frame).decode()
  assert (
    out.count("\n") == 10
  )  # the embedded newline is escaped: one line per row
  assert "a\\tb\\\\c\\nd\\re" in out


def test_vectorized_is_faster_than_per_cell_encoding():
  frame = _frame(60000)
  t0 = time.perf_counter()
  slow = _legacy(frame)
  t1 = time.perf_counter()
  fast = hi._host_frame_to_copy_bytes_vectorized(frame)
  t2 = time.perf_counter()
  assert fast == slow
  assert (t2 - t1) < (t1 - t0) * 0.7
