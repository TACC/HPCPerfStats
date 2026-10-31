"""K0 binary COPY decoder equals the row tuples the pandas path receives."""

import math
import struct

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar.jobframe import NameInterner
from hpcperfstats.analysis.metrics.lib.columnar.k0_decode import (
  COPY_SIGNATURE,
  PG_EPOCH_OFFSET_US,
  concat_chunks,
  decode_copy_binary,
  empty_chunk,
  encode_copy_binary,
)

T0 = 1_700_000_000_000_000
ROWS = [
  (T0, "h1", "host_cpu", "user", None, 10.0, 0.5, 5.0),
  (T0 + 1, "h1", "host_cpu", "user", "", None, None, None),
  (T0 + 2, "h2", "nvidia_gpu", "gpu_util", "3", float("nan"), 1.5, -2.0),
  (T0 + 3, "h2", "host_mem", "mem_used", "j:42", 1e30, 2**40, 1.0),
]


def _as_float4(x):
  return struct.unpack(">f", struct.pack(">f", x))[0]


def test_decode_matches_values_list_rows_including_nulls_and_nan():
  raw = encode_copy_binary(ROWS)
  ni = NameInterner()
  ch = decode_copy_binary(raw, ni)
  assert len(ch) == 4
  assert ch.time_us.tolist() == [T0, T0 + 1, T0 + 2, T0 + 3]
  assert [ni.hosts[i] for i in ch.host_id.tolist()] == ["h1", "h1", "h2", "h2"]
  assert [ni.types[i] for i in ch.type_id.tolist()] == [
    "host_cpu",
    "host_cpu",
    "nvidia_gpu",
    "host_mem",
  ]
  assert [ni.events[i] for i in ch.event_id.tolist()] == [
    "user",
    "user",
    "gpu_util",
    "mem_used",
  ]
  # NULL dev and '' dev both map to id 0; hardware positive; j: negative.
  assert ch.dev_id.tolist()[:2] == [0, 0]
  assert ch.dev_id[2] > 0 and ni.devs[ch.dev_id[2]] == "3"
  assert ch.dev_id[3] < 0 and ni.devs[-ch.dev_id[3]] == "j:42"
  # Floats: NULL -> NaN, NaN stays NaN, values widen exactly from float4.
  assert ch.value[0] == 10.0 and ch.arc[0] == 0.5 and ch.delta[0] == 5.0
  assert all(math.isnan(x) for x in (ch.value[1], ch.arc[1], ch.delta[1]))
  assert math.isnan(ch.value[2]) and ch.arc[2] == 1.5 and ch.delta[2] == -2.0
  assert ch.value[3] == _as_float4(1e30) and ch.arc[3] == float(2**40)
  assert ch.owned.tolist() == [1, 1, 1, 1]
  assert ch.value.dtype == np.float64 and ch.owned.dtype == np.uint8


def test_owned_column_is_decoded_when_present():
  rows = [(*ROWS[0], 1), (*ROWS[1], 0), (*ROWS[2], None)]
  raw = encode_copy_binary(rows, with_owned=True)
  ch = decode_copy_binary(raw, NameInterner(), has_owned_column=True)
  assert ch.owned.tolist() == [1, 0, 0]


def test_pg_epoch_offset_is_2000_01_01():
  # 2000-01-01T00:00:00Z is 946684800 s after the Unix epoch.
  assert PG_EPOCH_OFFSET_US == 946_684_800 * 1_000_000
  raw = encode_copy_binary([(PG_EPOCH_OFFSET_US, "h", "t", "e", None, 1, 1, 1)])
  payload = raw[len(COPY_SIGNATURE) + 8 + 2 + 4 :][:8]
  assert struct.unpack(">q", payload)[0] == 0


def test_rejects_bad_signature_and_field_count():
  with pytest.raises(ValueError, match="binary COPY"):
    decode_copy_binary(b"nope", NameInterner())
  raw = encode_copy_binary(ROWS[:1], with_owned=True)
  with pytest.raises(ValueError, match="field count"):
    decode_copy_binary(raw, NameInterner(), has_owned_column=False)


def test_empty_stream_and_concat():
  ch = decode_copy_binary(encode_copy_binary([]), NameInterner())
  assert len(ch) == 0
  ni = NameInterner()
  a = decode_copy_binary(encode_copy_binary(ROWS[:2]), ni)
  b = decode_copy_binary(encode_copy_binary(ROWS[2:]), ni)
  merged = concat_chunks([a, empty_chunk(), b])
  assert len(merged) == 4
  assert merged.time_us.tolist() == [r[0] for r in ROWS]
  assert len(concat_chunks([])) == 0
