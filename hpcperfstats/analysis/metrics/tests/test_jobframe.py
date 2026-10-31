"""JobFrame contract: dtypes, sort order, id tables, time axis."""

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
  ID_DTYPE,
  TIME_DTYPE,
  VALUE_DTYPE,
  JobFrame,
  NameInterner,
)

T0 = 1_700_000_000_000_000


def _records():
  return [
    (T0 + 60_000_000, "h2", "host_cpu", "user", None, 1, 20.0, 1.0, None),
    (T0, "h1", "host_cpu", "user", None, 1, 10.0, 0.5, 5.0),
    (T0 + 60_000_000, "h1", "host_cpu", "user", None, 1, 11.0, None, 6.0),
    (T0, "h1", "host_cpu", "system", None, 1, 1.0, 0.1, 0.0),
    (T0, "h1", "nvidia_gpu", "gpu_util", "1", 1, 50.0, None, None),
    (T0, "h1", "nvidia_gpu", "gpu_util", "0", 1, 40.0, None, None),
    (T0, "h2", "host_mem", "mem_used", "j:42", 1, 7.0, None, None),
    (T0, "h2", "host_mem", "mem_used", "j:7", 0, 9.0, None, None),
  ]


def test_dtypes_and_lengths_follow_the_contract():
  f = JobFrame.from_records("42", _records())
  f.check_contract()
  assert len(f) == 8 and f.n_rows == 8
  assert f.time_us.dtype == np.dtype(TIME_DTYPE)
  for col in ("host_id", "series_id", "dev_id"):
    assert getattr(f, col).dtype == np.dtype(ID_DTYPE)
  assert f.owned.dtype == np.dtype(np.uint8)
  for col in ("value", "arc", "delta"):
    assert getattr(f, col).dtype == np.dtype(VALUE_DTYPE)


def test_rows_sorted_by_series_host_dev_time():
  f = JobFrame.from_records("42", _records())
  keys = list(
    zip(
      f.series_id.tolist(),
      f.host_id.tolist(),
      f.dev_id.tolist(),
      f.time_us.tolist(),
      strict=True,
    )
  )
  assert keys == sorted(keys)
  # Series ids are assigned in sorted (type, event) order.
  assert f.series == [
    ("host_cpu", "system"),
    ("host_cpu", "user"),
    ("host_mem", "mem_used"),
    ("nvidia_gpu", "gpu_util"),
  ]


def test_id_tables_round_trip_names():
  f = JobFrame.from_records("42", _records())
  for i in range(len(f)):
    typ, event = f.series[f.series_id[i]]
    host = f.hosts[f.host_id[i]]
    dev = f.devs[abs(int(f.dev_id[i]))]
    assert (typ, event) in {(t, e) for t, e in f.series}
    assert host in ("h1", "h2")
    assert dev in ("", "0", "1", "j:42", "j:7")
  assert f.series_id_for("host_cpu", "user") == 1
  assert f.series_id_for("host_cpu", "nope") is None
  assert f.series_ids_for_type("host_cpu") == [0, 1]
  assert f.schema() == {
    "host_cpu": ["system", "user"],
    "host_mem": ["mem_used"],
    "nvidia_gpu": ["gpu_util"],
  }


def test_dev_ids_encode_empty_hardware_and_job_copies():
  f = JobFrame.from_records("42", _records())
  empty_rows = f.dev_id[f.series_id == f.series_id_for("host_cpu", "user")]
  assert set(empty_rows.tolist()) == {0}
  gpu_rows = f.dev_id[f.series_id == f.series_id_for("nvidia_gpu", "gpu_util")]
  assert all(d > 0 for d in gpu_rows.tolist())
  mem_rows = f.dev_id[f.series_id == f.series_id_for("host_mem", "mem_used")]
  assert all(d < 0 for d in mem_rows.tolist())
  assert all(f.devs[abs(d)].startswith("j:") for d in mem_rows.tolist())
  owned_mem = f.owned[f.series_id == f.series_id_for("host_mem", "mem_used")]
  assert sorted(owned_mem.tolist()) == [0, 1]


def test_null_floats_become_nan():
  f = JobFrame.from_records("42", _records())
  assert np.isnan(f.arc).sum() == 5
  assert np.isnan(f.delta).sum() == 5
  assert not np.isnan(f.value).any()


def test_time_axis_and_index():
  f = JobFrame.from_records("42", _records())
  assert f.time_axis().tolist() == [T0, T0 + 60_000_000]
  idx = f.time_index()
  assert idx.dtype == np.dtype(ID_DTYPE)
  assert np.array_equal(f.time_axis()[idx], f.time_us)


def test_check_contract_rejects_unsorted_or_wrong_dtype():
  f = JobFrame.from_records("42", _records())
  cols = (
    "time_us",
    "host_id",
    "series_id",
    "dev_id",
    "owned",
    "value",
    "arc",
    "delta",
  )
  bad = JobFrame(
    **{**f.__dict__, **{c: getattr(f, c)[::-1].copy() for c in cols}}
  )
  with pytest.raises(ValueError, match="sorted"):
    bad.check_contract()
  bad2 = JobFrame(**{**f.__dict__, "value": f.value.astype(np.float32)})
  with pytest.raises(ValueError, match="dtype"):
    bad2.check_contract()


def test_empty_frame_is_valid():
  f = JobFrame.from_records("1", [])
  f.check_contract()
  assert len(f) == 0 and f.series == [] and f.hosts == []
  assert f.time_axis().size == 0 and f.time_index().size == 0


def test_interner_is_stable_across_calls():
  ni = NameInterner()
  assert [ni.host("a"), ni.host("b"), ni.host("a")] == [0, 1, 0]
  assert [ni.dev(""), ni.dev("0"), ni.dev("j:1"), ni.dev("0")] == [0, 1, -2, 1]
  assert ni.devs == ["", "0", "j:1"]
