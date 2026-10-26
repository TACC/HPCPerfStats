"""Vectorized proc COPY encoding must match the per-cell encoder byte for byte."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from hpcperfstats.dbload.lib import sync_timedb_proc_data_insert as pi


def _legacy(frame: pd.DataFrame) -> bytes:
  """Encode with the original per-cell path (reference implementation)."""
  n = len(frame)
  columns = {
    c: frame[c].tolist() if c in frame.columns else [None] * n
    for c in pi.PROC_DATA_COPY_COLUMNS
  }
  lines = []
  for i in range(n):
    fields = []
    for col in pi.PROC_DATA_COPY_COLUMNS:
      value = columns[col][i]
      if col not in pi._PROC_PASSTHROUGH_COLUMNS:
        value = pi._proc_copy_scalar(value)
      fields.append(pi._sql_literal(value))
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def _frame(n: int, seed: int = 3) -> pd.DataFrame:
  rng = np.random.default_rng(seed)
  data: dict[str, object] = {
    "jid": rng.choice(["123", "456", None], n).astype(object),
    "host": rng.choice(["c001-001", "c001-002", "we\tird", "back\\slash"], n),
    "proc": rng.choice(["python", "a.out", "bash\nx", "mpi run", ""], n),
    "device": rng.choice(["", "gpu0", None, "nan"], n).astype(object),
  }
  for key in pi.HOST_PROC_KEYS:
    arr = rng.integers(0, 10**12, n).astype("float64")
    arr[rng.random(n) < 0.3] = np.nan
    arr[rng.random(n) < 0.05] = 0.0
    data[key] = arr
  return pd.DataFrame(data)


def test_vectorized_matches_per_cell_encoder_byte_for_byte():
  frame = _frame(4000)
  fast = pi._proc_frame_to_copy_bytes_vectorized(frame)
  assert fast is not None
  assert fast == _legacy(frame)


def test_integer_dtype_metric_columns_match():
  frame = _frame(200)
  frame["uid"] = np.arange(200, dtype="int64")
  frame["threads"] = np.ones(200, dtype="int64")
  assert pi._proc_frame_to_copy_bytes_vectorized(frame) == _legacy(frame)


def test_proc_frame_to_copy_bytes_uses_vectorized_result():
  frame = _frame(300)
  assert pi._proc_frame_to_copy_bytes(frame) == _legacy(frame)


def test_empty_frame_encodes_to_empty_bytes():
  assert pi._proc_frame_to_copy_bytes_vectorized(pd.DataFrame()) == b""
  assert pi._proc_frame_to_copy_bytes(pd.DataFrame()) == b""


def test_absent_column_falls_back_and_still_matches_legacy():
  frame = _frame(40).drop(columns=["vm_swap"])
  assert pi._proc_frame_to_copy_bytes_vectorized(frame) is None
  assert pi._proc_frame_to_copy_bytes(frame) == _legacy(frame)


def test_non_string_text_column_falls_back_and_still_matches_legacy():
  frame = _frame(40)
  frame["host"] = list(range(40))
  assert pi._proc_frame_to_copy_bytes_vectorized(frame) is None
  assert pi._proc_frame_to_copy_bytes(frame) == _legacy(frame)


def test_huge_metric_falls_back():
  frame = _frame(40)
  frame.loc[2, "vm_peak"] = 1e30
  assert pi._proc_frame_to_copy_bytes_vectorized(frame) is None


def test_object_metric_column_falls_back_and_still_matches_legacy():
  frame = _frame(40)
  frame["uid"] = pd.Series([1, None, 3, 4] * 10, dtype=object)
  assert pi._proc_frame_to_copy_bytes_vectorized(frame) is None
  assert pi._proc_frame_to_copy_bytes(frame) == _legacy(frame)


def test_special_characters_are_escaped_like_the_legacy_encoder():
  frame = _frame(10)
  frame.loc[0, "proc"] = "a\tb\\c\nd\re"
  out = pi._proc_frame_to_copy_bytes(frame).decode()
  assert out.count("\n") == 10
  assert "a\\tb\\\\c\\nd\\re" in out


def test_vectorized_is_faster_than_per_cell_encoding():
  frame = _frame(60000)
  t0 = time.perf_counter()
  slow = _legacy(frame)
  t1 = time.perf_counter()
  fast = pi._proc_frame_to_copy_bytes_vectorized(frame)
  t2 = time.perf_counter()
  assert fast == slow
  assert (t2 - t1) < (t1 - t0) * 0.7
