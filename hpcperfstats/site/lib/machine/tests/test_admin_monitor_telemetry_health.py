"""Unit tests for admin_monitor_telemetry_health payload and timeout helpers."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from hpcperfstats.site.lib.machine import admin_monitor_telemetry_health as th

pytestmark = pytest.mark.machine_unit_mock


def test_build_payload_reports_all_zero_and_missing_cores():
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 12,
        "has_nonzero": False,
      },
      {
        "type": "host_mem",
        "event": "used",
        "row_count": 12,
        "has_nonzero": True,
      },
    ],
    computed_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    expected_core_types=("host_cpu", "host_mem", "host_block"),
  )
  assert payload["window_hours"] == th.WINDOW_HOURS
  assert payload["timed_out"] is False
  assert payload["all_zero_events"] == [
    {"type": "host_cpu", "event": "user", "row_count": 12}
  ]
  assert payload["missing_core_types"] == ["host_block"]
  assert payload["ok_summary"]["nonzero_type_event_pairs"] == 1
  assert payload["truncated"] is False
  assert any(f["kind"] == "all_zero_core_event" for f in payload["findings"])
  assert any(f["kind"] == "missing_core_type" for f in payload["findings"])
  assert "Actionable findings" in payload["monitor_handoff_markdown"]


def test_build_payload_excludes_error_names_are_caller_filtered():
  """Caller SQL excludes *error*; informational IB zeros stay out of default table."""
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_ib",
        "event": "port_xmit_data",
        "row_count": 5,
        "has_nonzero": False,
      }
    ],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    expected_core_types=("host_cpu",),
  )
  assert payload["all_zero_events"] == []
  assert "host_cpu" in payload["missing_core_types"]
  assert any(
    f["kind"] == "all_zero_other_event" and f["event"] == "port_xmit_data"
    for f in payload["findings"]
  )


def test_build_payload_non_zero_value_or_arc_clears_pair():
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 3,
        "has_nonzero": True,
      }
    ],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    expected_core_types=("host_cpu",),
  )
  assert payload["all_zero_events"] == []
  assert payload["missing_core_types"] == []
  assert payload["ok_summary"]["nonzero_type_event_pairs"] == 1


def test_build_payload_empty_window_lists_missing_cores_not_healthy():
  payload = th.build_telemetry_health_payload(
    [],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    expected_core_types=("host_cpu", "host_mem"),
  )
  assert payload["all_zero_events"] == []
  assert payload["missing_core_types"] == ["host_cpu", "host_mem"]
  assert payload["ok_summary"]["nonzero_type_event_pairs"] == 0
  assert (
    "No all-zero or missing-core anomalies"
    not in payload["ok_summary"]["scanned_note"]
  )


def test_build_payload_timeout_soft_fail_empty_lists():
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 99,
        "has_nonzero": False,
      }
    ],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    timed_out=True,
    error="timed out",
  )
  assert payload["timed_out"] is True
  assert payload["all_zero_events"] == []
  assert payload["missing_core_types"] == []
  assert payload["error"] == "timed out"


def test_build_payload_truncates_all_zero_list():
  rows = [
    {
      "type": "host_cpu",
      "event": f"e{i}",
      "row_count": 1,
      "has_nonzero": False,
    }
    for i in range(5)
  ]
  payload = th.build_telemetry_health_payload(
    rows,
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    expected_core_types=("host_cpu",),
    all_zero_limit=3,
  )
  assert len(payload["all_zero_events"]) == 3
  assert payload["truncated"] is True


def test_build_payload_core_zero_actionable_ib_informational():
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 9,
        "has_nonzero": False,
      },
      {
        "type": "host_ib",
        "event": "port_xmit_data",
        "row_count": 9,
        "has_nonzero": False,
      },
    ],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    expected_core_types=("host_cpu",),
    hosts_sampled_fqdns=["n001.example.com"],
    monitor_identities=[
      {
        "fqdn": "n001.example.com",
        "package_version": "3.0",
        "uname": "Linux x86_64",
        "capability_slug": None,
        "schema_types": ["host_cpu", "host_ib"],
        "updated_at": 1,
      }
    ],
  )
  assert payload["all_zero_events"] == [
    {"type": "host_cpu", "event": "user", "row_count": 9}
  ]
  kinds = {f["kind"] for f in payload["findings"]}
  assert "all_zero_core_event" in kinds
  assert "all_zero_other_event" in kinds
  assert "signature_absent" not in kinds
  md = payload["monitor_handoff_markdown"]
  sampled_fqdn = payload["hosts_sampled_fqdns"][0]
  assert md.find(sampled_fqdn) >= 0
  assert "slug pending" in md.lower() or "slug pending RPM" in md
  assert (
    "host_ib" not in md.split("## Actionable findings")[1].split("## Out")[0]
  )


def test_build_payload_incomplete_scan_markdown_banner():
  payload = th.build_telemetry_health_payload(
    [],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    timed_out=True,
    error="timed out",
    hosts_sampled_fqdns=["a.example.com"],
  )
  assert payload["timed_out"] is True
  assert payload["findings"][0]["kind"] == "incomplete_scan"
  assert "do not conclude" in payload["monitor_handoff_markdown"].lower()


def test_build_payload_signature_absent_when_identity_missing():
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 1,
        "has_nonzero": True,
      }
    ],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    expected_core_types=("host_cpu",),
    hosts_sampled_fqdns=["orphan.example.com"],
    monitor_identities=[],
  )
  assert any(
    f["kind"] == "signature_absent" and f["fqdn"] == "orphan.example.com"
    for f in payload["findings"]
  )


def test_is_statement_timeout_detects_pg_message():
  assert th._is_statement_timeout(
    Exception("canceling statement due to statement timeout")
  )
  assert not th._is_statement_timeout(Exception("syntax error"))


def test_compute_telemetry_health_soft_fails_on_timeout():
  with (
    patch.object(th.cache, "get", return_value=None),
    patch.object(th.cache, "set"),
    patch.object(th.connection, "vendor", "postgresql"),
    patch.object(
      th,
      "_fetch_type_event_aggregates",
      side_effect=Exception("canceling statement due to statement timeout"),
    ),
  ):
    payload = th.compute_telemetry_health(force_refresh=True)
  assert payload["timed_out"] is True
  assert payload["all_zero_events"] == []
  assert "timed out" in (payload.get("error") or "").lower()


def test_compute_telemetry_health_timeout_logs_without_traceback():
  with (
    patch.object(th.cache, "get", return_value=None),
    patch.object(th.cache, "set"),
    patch.object(th.connection, "vendor", "postgresql"),
    patch.object(
      th,
      "_fetch_type_event_aggregates",
      side_effect=Exception("canceling statement due to statement timeout"),
    ),
    patch.object(th.logger, "warning") as warn,
  ):
    th.compute_telemetry_health(force_refresh=True)
  assert warn.called
  _args, kwargs = warn.call_args
  assert kwargs.get("exc_info") in (None, False)


def test_compute_telemetry_health_valkey_empty_is_incomplete():
  marker = "UNIQUE_VALKEY_EMPTY_EXC_DETAIL_xyz"
  with (
    patch.object(th.cache, "get", return_value=None),
    patch.object(th.cache, "set"),
    patch.object(th.connection, "vendor", "postgresql"),
    patch.object(
      th,
      "_fetch_type_event_aggregates",
      side_effect=th.EmptyRecentHostInventory(marker),
    ) as fetch,
  ):
    payload = th.compute_telemetry_health(force_refresh=True)
  assert payload["timed_out"] is True
  assert payload["all_zero_events"] == []
  assert payload["missing_core_types"] == []
  err = payload.get("error") or ""
  assert "recent_host" in err.lower()
  assert marker not in err
  assert payload["ok_summary"]["hosts_sampled"] == 0
  assert fetch.called


def test_compute_telemetry_health_generic_failure_omits_exc_text():
  marker = "UNIQUE_TELEMETRY_FAILURE_DETAIL_abc"
  with (
    patch.object(th.cache, "get", return_value=None),
    patch.object(th.cache, "set"),
    patch.object(th.connection, "vendor", "postgresql"),
    patch.object(
      th,
      "_fetch_type_event_aggregates",
      side_effect=RuntimeError(marker),
    ),
  ):
    payload = th.compute_telemetry_health(force_refresh=True)
  assert payload["timed_out"] is True
  err = payload.get("error") or ""
  assert "failed" in err.lower()
  assert "incomplete" in err.lower()
  assert marker not in err


def test_compute_telemetry_health_returns_cached():
  cached = {"window_hours": 12, "timed_out": False, "all_zero_events": []}
  with patch.object(th.cache, "get", return_value=cached):
    assert th.compute_telemetry_health() is cached


def test_merge_type_event_batches_sums_counts_and_ors_nonzero():
  merged = th._merge_type_event_batches(
    [
      [
        {
          "type": "host_cpu",
          "event": "user",
          "row_count": 2,
          "has_nonzero": False,
        },
        {
          "type": "host_mem",
          "event": "used",
          "row_count": 1,
          "has_nonzero": True,
        },
      ],
      [
        {
          "type": "host_cpu",
          "event": "user",
          "row_count": 3,
          "has_nonzero": True,
        },
      ],
    ]
  )
  by_key = {(r["type"], r["event"]): r for r in merged}
  assert by_key[("host_cpu", "user")]["row_count"] == 5
  assert by_key[("host_cpu", "user")]["has_nonzero"] is True
  assert by_key[("host_mem", "used")]["row_count"] == 1
  assert by_key[("host_mem", "used")]["has_nonzero"] is True


def test_fetch_aggregates_scopes_sql_to_valkey_hosts():
  exec_log: list[tuple[str, object]] = []

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      exec_log.append((sql, params))
      self._rows = []
      if "GROUP BY" in sql:
        self._rows = [("host_cpu", "user", 0, 4, True)]

    def fetchall(self):
      return getattr(self, "_rows", [])

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.alias = "default"
  fake_conn.cursor = lambda: FakeCursor()

  hosts = [f"c{i:03d}.example.com" for i in range(5)]
  with (
    patch.object(th, "transaction") as txn,
    patch.object(th, "connection", fake_conn),
    patch.object(th, "list_recent_host_fqdns_from_valkey", return_value=hosts),
  ):
    txn.atomic.return_value = contextlib.nullcontext()
    rows, sampled = th._fetch_type_event_aggregates(
      window_hours=12,
      host_sample_limit=16,
      host_batch_size=8,
    )
  assert len(sampled) == 5
  assert rows[0]["type"] == "host_cpu"
  group_sql = [e for e in exec_log if e[0] and "GROUP BY" in e[0]]
  assert group_sql
  assert all("host = ANY" in e[0] for e in group_sql)
  assert all(
    "FROM host_data\n        WHERE time >=" not in e[0]
    and "WHERE host = ANY" in e[0]
    for e in group_sql
  )


def test_fetch_aggregates_caps_host_sample():
  exec_log: list[tuple[str, object]] = []

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      exec_log.append((sql, params))
      self._rows = []

    def fetchall(self):
      return []

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.alias = "default"
  fake_conn.cursor = lambda: FakeCursor()

  hosts = [f"h{i:03d}.example.com" for i in range(200)]
  with (
    patch.object(th, "transaction") as txn,
    patch.object(th, "connection", fake_conn),
    patch.object(th, "list_recent_host_fqdns_from_valkey", return_value=hosts),
  ):
    txn.atomic.return_value = contextlib.nullcontext()
    _rows, sampled = th._fetch_type_event_aggregates(
      window_hours=12,
      host_sample_limit=th.HOST_SAMPLE_LIMIT,
      host_batch_size=th.HOST_BATCH_SIZE,
    )
  assert len(sampled) == th.HOST_SAMPLE_LIMIT
  group_params = [
    e[1] for e in exec_log if e[0] and "GROUP BY" in e[0] and e[1] is not None
  ]
  hosts_sent = []
  for params in group_params:
    hosts_sent.extend(params[0])
  assert len(hosts_sent) == th.HOST_SAMPLE_LIMIT
  assert len(group_params) == 2  # 16 hosts / batch 8


def test_fetch_aggregates_merges_batches():
  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      self._params = params
      self._rows = []
      if params and "GROUP BY" in sql:
        chunk = params[0]
        if chunk and chunk[0].startswith("a"):
          self._rows = [("host_cpu", "user", 0, 2, False)]
        else:
          self._rows = [("host_cpu", "user", 0, 3, True)]

    def fetchall(self):
      return getattr(self, "_rows", [])

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.alias = "default"
  fake_conn.cursor = lambda: FakeCursor()

  hosts = [f"a{i}.example.com" for i in range(8)] + [
    f"b{i}.example.com" for i in range(8)
  ]
  with (
    patch.object(th, "transaction") as txn,
    patch.object(th, "connection", fake_conn),
    patch.object(th, "list_recent_host_fqdns_from_valkey", return_value=hosts),
  ):
    txn.atomic.return_value = contextlib.nullcontext()
    rows, sampled = th._fetch_type_event_aggregates(
      window_hours=12,
      host_sample_limit=16,
      host_batch_size=8,
    )
  assert len(sampled) == 16
  assert len(rows) == 1
  assert rows[0]["row_count"] == 5
  assert rows[0]["has_nonzero"] is True


def test_fetch_aggregates_raises_on_empty_valkey():
  with patch.object(th, "list_recent_host_fqdns_from_valkey", return_value=[]):
    with pytest.raises(th.EmptyRecentHostInventory):
      th._fetch_type_event_aggregates(window_hours=12)


def test_build_payload_includes_hosts_sampled():
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 1,
        "has_nonzero": True,
      }
    ],
    computed_at=datetime(2024, 6, 1, tzinfo=UTC),
    expected_core_types=("host_cpu",),
    hosts_sampled=16,
  )
  assert payload["ok_summary"]["hosts_sampled"] == 16
  assert "16" in payload["ok_summary"]["scanned_note"]


def test_expected_core_types_include_host_cpu_mem():
  cores = th.expected_core_types()
  assert "host_cpu" in cores
  assert "host_mem" in cores


def test_build_payload_sentinel_only_core_event_is_not_all_zero():
  """A core key whose rows are all monitor sentinels is unreadable, not
  all-zero: it gets a sentinel_core_event with per-code counts instead."""
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 12,
        "has_nonzero": False,
        "sentinel_codes": {-3: 8, -2: 4},
      },
    ],
    computed_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    expected_core_types=("host_cpu",),
  )
  kinds = [f["kind"] for f in payload["findings"]]
  assert "all_zero_core_event" not in kinds
  (sent,) = [
    f for f in payload["findings"] if f["kind"] == "sentinel_core_event"
  ]
  assert sent["sentinel_codes"] == {"NOT_PRESENT": 4, "READ_FAILED": 8}
  assert payload["all_zero_events"] == []


def test_merge_batches_sums_sentinel_codes():
  merged = th._merge_type_event_batches(
    [
      [
        {
          "type": "a",
          "event": "e",
          "row_count": 2,
          "has_nonzero": False,
          "sentinel_codes": {-3: 2},
        }
      ],
      [
        {
          "type": "a",
          "event": "e",
          "row_count": 3,
          "has_nonzero": True,
          "sentinel_codes": {-3: 1, -7: 2},
        }
      ],
    ]
  )
  assert merged == [
    {
      "type": "a",
      "event": "e",
      "row_count": 5,
      "has_nonzero": True,
      "sentinel_codes": {-3: 3, -7: 2},
    }
  ]


def test_telemetry_sql_counts_sentinels_as_not_nonzero():
  """has_nonzero only counts data (value >= 0); codes are bucketed."""
  src = th._fetch_type_event_aggregates.__code__.co_consts
  sql = next(c for c in src if isinstance(c, str) and "BOOL_OR" in c)
  assert "(value >= 0 AND value <> 0)" in sql
  assert "GROUP BY type, event, code" in sql
  assert (
    th._SENTINEL_CODE_SQL == "CASE WHEN value < 0 THEN value::int ELSE 0 END"
  )


def test_build_payload_partial_sentinel_zero_core_event_still_all_zero():
  payload = th.build_telemetry_health_payload(
    [
      {
        "type": "host_cpu",
        "event": "user",
        "row_count": 12,
        "has_nonzero": False,
        "sentinel_codes": {-1: 2},
      },
    ],
    computed_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    expected_core_types=("host_cpu",),
  )
  kinds = [f["kind"] for f in payload["findings"]]
  assert "all_zero_core_event" in kinds and "sentinel_core_event" in kinds


_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _run_rows(*runs):
  """``values_list`` rows: (host, type, event, code, start_h, end_h ago)."""
  from datetime import timedelta

  return [
    (h, t, e, c, _NOW - timedelta(hours=a), _NOW - timedelta(hours=b))
    for h, t, e, c, a, b in runs
  ]


class _FakeRuns:
  """Stands in for ``host_sentinel_summary.objects`` and records filters."""

  def __init__(self, rows):
    self.rows = rows
    self.calls = []

  def filter(self, **kwargs):
    self.calls.append(kwargs)
    hosts = set(kwargs["host__in"])
    out = MagicMock()
    out.values_list.return_value = [r for r in self.rows if r[0] in hosts]
    return out


def _shares(rows, fqdns=("n1.x",)):
  from hpcperfstats.site.lib.machine import models

  fake = _FakeRuns(rows)
  with (
    patch.object(models.host_sentinel_summary, "objects", fake),
    patch.object(
      th.connection, "cursor", side_effect=AssertionError("host_data SQL")
    ),
  ):
    out = th._fetch_sentinel_run_shares(list(fqdns), window_hours=12, now=_NOW)
  return out, fake


def test_persistent_not_updated_above_the_frac():
  """-1 runs over 9 of 12 hours (two overlapping runs unioned) -> finding."""
  shares, _fake = _shares(
    _run_rows(
      ("n1.x", "host_ib", "rx", -1, 10, 4),
      ("n1.x", "host_ib", "rx", -1, 6, 1),
    )
  )
  assert shares[0]["share"] == pytest.approx(9 / 12)
  found = th.sentinel_run_findings(shares, not_updated_frac=0.5)
  assert [f["kind"] for f in found] == ["persistent_not_updated"]
  assert found[0]["fqdn"] == "n1.x" and found[0]["type"] == "host_ib"


def test_persistent_not_updated_none_for_brief_not_updated():
  """One -1 tick after a restart (zero-length run) is not a finding."""
  shares, _fake = _shares(_run_rows(("n1.x", "host_cpu", "user", -1, 3, 3)))
  assert th.sentinel_run_findings(shares, not_updated_frac=0.5) == []


def test_persistent_not_updated_from_side_table_only():
  """Shares read host_sentinel_summary only; no raw cursor (host_data)."""
  from hpcperfstats.site.lib.machine.models import host_sentinel_summary

  shares, fake = _shares(
    _run_rows(("n1.x", "host_ib", "rx", -1, 20, 0)), fqdns=("n1.x", "n2.x")
  )
  assert fake.calls and all("host__in" in c for c in fake.calls)
  assert shares[0]["share"] == pytest.approx(1.0)  # clipped to the window
  sql = str(
    host_sentinel_summary.objects.filter(
      host__in=["n1.x"], end_time__gte=_NOW, start_time__lte=_NOW
    ).query
  )
  assert "host_sentinel_summary" in sql and "host_data" not in sql


def test_persistent_not_updated_threshold_from_portal_ini():
  """Default threshold is [PORTAL] telemetry_persistent_not_updated_frac."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  assert ("PORTAL", "telemetry_persistent_not_updated_frac", "0.5") in (
    cfg.INI_OPTION_REGISTRY
  )
  row = {"fqdn": "h", "type": "t", "event": "e", "code": -1, "share": 0.6}
  with patch.object(
    cfg, "get_telemetry_persistent_not_updated_frac", return_value=0.7
  ):
    assert th.sentinel_run_findings([row]) == []
  with patch.object(
    cfg, "get_telemetry_persistent_not_updated_frac", return_value=0.5
  ):
    assert th.sentinel_run_findings([row])[0]["kind"] == (
      "persistent_not_updated"
    )


def test_transient_sentinel_rate_on_high_backoff_or_read_failed():
  shares = [
    {
      "fqdn": "n1.x",
      "type": "host_ib",
      "event": "rx",
      "code": -3,
      "share": 0.04,
    },
    {
      "fqdn": "n1.x",
      "type": "host_ib",
      "event": "rx",
      "code": -9,
      "share": 0.04,
    },
    {
      "fqdn": "n2.x",
      "type": "host_ib",
      "event": "rx",
      "code": -3,
      "share": 0.01,
    },
    {
      "fqdn": "n3.x",
      "type": "host_ib",
      "event": "rx",
      "code": -5,
      "share": 0.9,
    },
  ]
  found = th.sentinel_run_findings(shares, not_updated_frac=0.5)
  assert [(f["kind"], f["fqdn"]) for f in found] == [
    ("transient_sentinel_rate", "n1.x")
  ]
  assert found[0]["sentinel_codes"] == {"READ_FAILED": 4, "BACKOFF": 4}


def test_compute_telemetry_health_adds_persistent_not_updated_finding():
  """The postgres scan path feeds side-table shares into the payload."""
  with (
    patch.object(th.cache, "get", return_value=None),
    patch.object(th.cache, "set"),
    patch.object(th.connection, "vendor", "postgresql"),
    patch.object(
      th, "_fetch_type_event_aggregates", return_value=([], ["n1.x"])
    ),
    patch.object(th, "load_monitor_identities_for_hosts", return_value=[]),
    patch.object(th, "get_valkey_client"),
    patch.object(
      th,
      "_fetch_sentinel_run_shares",
      return_value=[
        {
          "fqdn": "n1.x",
          "type": "host_ib",
          "event": "rx",
          "code": -1,
          "share": 1.0,
        }
      ],
    ),
  ):
    payload = th.compute_telemetry_health(force_refresh=True)
  kinds = [f["kind"] for f in payload["findings"]]
  assert "persistent_not_updated" in kinds
