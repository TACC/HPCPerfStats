"""Unit tests for analysis.metrics.metrics (_Schema, _EventIndex, _Host, avg_freq).

"""
import numpy as np
import pytest

from hpcperfstats.analysis.metrics.metrics import (
    _EventIndex,
    _Host,
    _Schema,
    avg_freq,
)


def test_event_index_stores_index():
  """_EventIndex stores and exposes .index."""
  idx = _EventIndex(3)
  assert idx.index == 3


def test_schema_events_and_desc():
  """_Schema builds events list and desc from event names."""
  events = ["a", "b", "c"]
  schema = _Schema(events)
  assert schema.events == ["a", "b", "c"]
  assert schema.desc == "a b c\n"


def test_schema_getitem_returns_event_index():
  """_Schema.__getitem__ returns _EventIndex with correct index."""
  schema = _Schema(["x", "y", "z"])
  ei = schema["y"]
  assert isinstance(ei, _EventIndex)
  assert ei.index == 1


def test_schema_getitem_raises_for_unknown_event():
  """_Schema.__getitem__ raises KeyError for unknown event."""
  schema = _Schema(["a", "b"])
  with pytest.raises(KeyError):
    schema["c"]


def test_host_starts_with_empty_stats():
  """_Host has empty stats dict by default."""
  h = _Host()
  assert h.stats == {}


def test_avg_freq_returns_none_when_no_pmc():
  """avg_freq.compute_metric returns (None, typename, units) when get_type('pmc') has no schema."""
  class MockU:
    def get_type(self, typename):
      return None, {}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert value is None
  assert typename == "pmc"
  assert units == "GHz"


def test_avg_freq_compute_metric():
  """avg_freq.compute_metric returns (value, typename, units) from cycles and freq."""
  schema = _Schema(["CLOCKS_UNHALTED_CORE", "CLOCKS_UNHALTED_REF"])
  stats = np.array([[0.0, 0.0], [100.0, 200.0]], dtype=np.float64)

  class MockU:
    freq = 2.5

    def get_type(self, typename):
      return schema, {"host1": stats}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert value is not None
  assert abs(value - 1.25) < 1e-9
  assert typename == "pmc"
  assert units == "GHz"


def test_avg_freq_returns_none_when_cycles_ref_zero():
  """avg_freq.compute_metric returns None when cycles_ref is zero."""
  schema = _Schema(["CLOCKS_UNHALTED_CORE", "CLOCKS_UNHALTED_REF"])
  stats = np.array([[0.0, 0.0], [100.0, 0.0]], dtype=np.float64)

  class MockU:
    freq = 2.5

    def get_type(self, typename):
      return schema, {"host1": stats}

  u = MockU()
  value, typename, units = avg_freq().compute_metric(u)
  assert value is None
  assert typename == "pmc"
  assert units == "GHz"
