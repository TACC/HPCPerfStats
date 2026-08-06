"""Unit tests for cheap host_data freshness helpers."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import MagicMock

import pytest

from hpcperfstats.site.lib.machine import host_data_latest


@pytest.mark.machine_unit_mock
def test_latest_sample_time_by_host_empty():
  assert host_data_latest.latest_sample_time_by_host([]) == {}
  assert host_data_latest.latest_sample_time_by_host(None) == {}


@pytest.mark.machine_unit_mock
def test_latest_sample_time_by_host_postgresql_lateral_limit_1(monkeypatch):
  exec_log = []
  monkeypatch.setattr(
      host_data_latest.transaction,
      "atomic",
      lambda using=None: contextlib.nullcontext(),
  )
  monkeypatch.setattr(host_data_latest, "HOST_LAST_TIME_LOOKUP_BATCH", 2)

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      exec_log.append((sql, params))
      if "unnest" in sql.lower() and params and params[0]:
        ts = datetime(2025, 1, 1, 12, 0, 5, tzinfo=dt_timezone.utc)
        self._rows = [(h, ts) for h in params[0]]
      else:
        self._rows = []

    def fetchall(self):
      return getattr(self, "_rows", [])

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.alias = "default"
  fake_ops = MagicMock()
  fake_ops.quote_name = lambda name: '"%s"' % str(name).replace('"', '""')
  fake_conn.ops = fake_ops
  fake_conn.cursor = lambda: FakeCursor()
  handler = MagicMock()
  handler.__getitem__ = lambda self, name: fake_conn
  monkeypatch.setattr(host_data_latest, "connections", handler)

  out = host_data_latest.latest_sample_time_by_host(["b.example", "a.example"])
  assert sorted(out.keys()) == ["a.example", "b.example"]
  lateral = [e for e in exec_log if e[0] and "LATERAL" in e[0]]
  assert lateral
  assert all("LIMIT 1" in e[0] for e in lateral)
  assert all("DISTINCT ON" not in e[0] for e in lateral)


@pytest.mark.machine_unit_mock
def test_newest_host_data_sample_time_uses_order_by_limit(monkeypatch):
  calls = {}
  expected = datetime(2026, 8, 4, 0, 55, tzinfo=dt_timezone.utc)
  fixed_now = datetime(2026, 8, 4, 12, 0, tzinfo=dt_timezone.utc)
  monkeypatch.setattr(host_data_latest.timezone, "now", lambda: fixed_now)

  class FakeQS:
    def filter(self, **kwargs):
      calls["filter"] = kwargs
      return self

    def order_by(self, *args):
      calls["order_by"] = args
      return self

    def values_list(self, *args, **kwargs):
      calls["values_list"] = (args, kwargs)
      return self

    def first(self):
      return expected

  qs = FakeQS()
  monkeypatch.setattr(host_data_latest.host_data, "objects", MagicMock())
  host_data_latest.host_data.objects.filter = qs.filter

  out = host_data_latest.newest_host_data_sample_time()
  assert out == expected
  assert calls["order_by"] == ("-time",)
  assert calls["filter"]["time__gt"] == fixed_now - timedelta(hours=3)

@pytest.mark.machine_unit_mock
def test_latest_sample_time_by_host_in_window_uses_short_window(monkeypatch):
  captured = {}

  class AnnotateQS:
    def __iter__(self):
      return iter(
          [
              {
                  "host": "n1.example.org",
                  "last_time": datetime(2026, 8, 4, tzinfo=dt_timezone.utc),
              }
          ]
      )

  class ValuesQS:
    def annotate(self, **kwargs):
      captured["annotate"] = kwargs
      return AnnotateQS()

  class FilterQS:
    def values(self, *args):
      captured["values"] = args
      return ValuesQS()

  def fake_filter(**kwargs):
    captured["filter"] = kwargs
    return FilterQS()

  monkeypatch.setattr(
      host_data_latest.host_data.objects,
      "filter",
      fake_filter,
  )
  fixed_now = datetime(2026, 8, 4, 12, 0, tzinfo=dt_timezone.utc)
  monkeypatch.setattr(
      host_data_latest.timezone,
      "now",
      lambda: fixed_now,
  )

  out = host_data_latest.latest_sample_time_by_host_in_window()
  assert set(out.keys()) == {"n1.example.org"}
  time_gte = captured["filter"]["time__gte"]
  assert fixed_now - time_gte == timedelta(hours=3)
  # Must not be an 8-day window.
  assert (fixed_now - time_gte).days < 2


@pytest.mark.machine_unit_mock
def test_format_host_data_newest_iso():
  assert host_data_latest.format_host_data_newest_iso(None) is None
  dt = datetime(2026, 8, 4, 0, 55, tzinfo=dt_timezone.utc)
  assert "2026-08-04" in host_data_latest.format_host_data_newest_iso(dt)
