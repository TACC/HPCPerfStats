"""Unit tests for analysis.metrics.update_metrics (_iter_chunked_pks).

"""
from datetime import datetime

import pytest

from hpcperfstats.analysis.metrics.update_metrics import _iter_chunked_pks
from hpcperfstats.analysis.metrics import update_metrics


def test_iter_chunked_pks_empty_queryset():
  """_iter_chunked_pks yields nothing for empty pk iterator."""
  class EmptyQs:
    def values_list(self, *args, **kwargs):
      return self

    def iterator(self, chunk_size=1):
      return iter([])

  qs = EmptyQs()
  chunks = list(_iter_chunked_pks(qs, 2))
  assert chunks == []


def test_iter_chunked_pks_single_chunk():
  """_iter_chunked_pks yields one (pk_list, total) when pks fit in one chunk."""
  class Qs:
    def values_list(self, *args, **kwargs):
      return self

    def iterator(self, chunk_size=10):
      return iter([1, 2, 3])

  qs = Qs()
  chunks = list(_iter_chunked_pks(qs, 10))
  assert len(chunks) == 1
  assert chunks[0][0] == [1, 2, 3]
  assert chunks[0][1] == 3


def test_iter_chunked_pks_multiple_chunks():
  """_iter_chunked_pks yields (pk_list, total_so_far) for each chunk."""
  class Qs:
    def values_list(self, *args, **kwargs):
      return self

    def iterator(self, chunk_size=2):
      return iter([10, 20, 30, 40, 50])

  qs = Qs()
  chunks = list(_iter_chunked_pks(qs, 2))
  assert len(chunks) == 3
  assert chunks[0] == ([10, 20], 2)
  assert chunks[1] == ([30, 40], 4)
  assert chunks[2] == ([50], 5)


def test_notify_parent_if_sigterm_sends_sigchld(monkeypatch):
  calls = []
  monkeypatch.setattr(
      update_metrics, "send_sigchld_to_parent", lambda: calls.append("sigchld"))

  update_metrics._notify_parent_if_sigterm([True])
  assert calls == ["sigchld"]


def test_default_metrics_date_range_seven_days(monkeypatch):
  """No-arg CLI default spans seven calendar days through today (local midnight bounds)."""
  monkeypatch.setattr(
      update_metrics,
      "_today_datetime",
      lambda: datetime(2025, 3, 23, 15, 30, 0),
  )
  start, end = update_metrics._default_metrics_date_range()
  assert end == datetime(2025, 3, 23, 0, 0, 0)
  assert start == datetime(2025, 3, 17, 0, 0, 0)


def test_install_sigterm_handler_sets_flag_and_raises(monkeypatch):
  monkeypatch.setattr(update_metrics.signal, "getsignal", lambda sig: "prev")
  monkeypatch.setattr(update_metrics.signal, "signal", lambda sig, h: None)

  update_metrics.shutdown_requested[0] = False

  previous_handler, sigterm_received, handler = update_metrics._install_sigterm_handler(
      exit_code=143
  )
  assert previous_handler == "prev"
  assert sigterm_received[0] is False

  with pytest.raises(SystemExit) as excinfo:
    handler(update_metrics.signal.SIGTERM, None)
  assert sigterm_received[0] is True
  assert excinfo.value.code == 143
  assert update_metrics.shutdown_requested[0] is True
