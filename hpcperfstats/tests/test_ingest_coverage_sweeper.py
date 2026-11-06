"""Ingest coverage sweeper: per-host windows, status, switch, overrun."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from hpcperfstats.dbload import ingest_coverage_sweeper as entry
from hpcperfstats.dbload.lib import conf_parser as cfg, ingest_coverage as ic
from hpcperfstats.lib.ingest_rotation import ingest_rotation_key

NOW = 1_800_000_000.0
HOST_A = "c101-001.example.com"
HOST_B = "c101-002.example.com"
HOST_C = "c102-001.example.com"


class FakeValkey:
  def __init__(self):
    self.data = {}
    self.ttl = {}
    self.sets = []
    self.lists = {}

  def get(self, key):
    return self.data.get(key)

  def set(self, key, value, ex=None):
    self.data[key] = value
    self.ttl[key] = ex
    self.sets.append(key)

  def lrange(self, key, start, end):
    return self.lists.get(key, [])[start : end + 1]

  def doc(self, key):
    return json.loads(self.data[key])


class Clock:
  def __init__(self, start=NOW, step=1.0):
    self.t = start
    self.step = step

  def __call__(self):
    value = self.t
    self.t += self.step
    return value


def _dt(epoch):
  return datetime.fromtimestamp(epoch, tz=UTC)


def _rot(client, host, rotated_at, closed_last):
  client.lists.setdefault(ingest_rotation_key(host), []).insert(
    0,
    json.dumps(
      {"rotated_at": rotated_at, "closed_last_sample_at": closed_last}
    ),
  )


def _sweep(client, fqdns, latest, scan=None, **kw):
  calls = {"latest": [], "scan": []}

  def latest_fn(hosts, *, since):
    calls["latest"].append((list(hosts), since))
    return latest

  def scan_fn(host, lo, hi, *, timeout_ms):
    calls["scan"].append((host, lo, hi, timeout_ms))
    if scan is not None:
      return scan(host)
    return [("host_cpu", "user", 60, True), ("host_cpu", "nice", 60, False)]

  opts = {"interval_s": 3600, "window_minutes": 60, "timeout_ms": 60000}
  opts.update(kw)
  beat = ic.run_sweep(
    client=client,
    fqdns=fqdns,
    latest_fn=latest_fn,
    scan_fn=scan_fn,
    now_fn=kw.pop("now_fn", None) or Clock(),
    log_fn=lambda msg: None,
    **{k: v for k, v in opts.items() if k != "now_fn"},
  )
  return beat, calls


def test_ingest_coverage_sweeper_per_host_windows_and_sig_dedup():
  client = FakeValkey()
  newest_a = _dt(NOW - 300)
  newest_b = _dt(NOW - 4 * 3600)  # 4 h behind: own window, not the fleet's
  beat, calls = _sweep(
    client, [HOST_A, HOST_B], {HOST_A: newest_a, HOST_B: newest_b}
  )
  assert len(calls["latest"]) == 1, "one batched newest-sample probe"
  since = calls["latest"][0][1]
  assert since == _dt(NOW) - timedelta(days=7)
  by_host = {c[0]: c for c in calls["scan"]}
  assert by_host[HOST_B][1:3] == (newest_b - timedelta(minutes=60), newest_b)
  assert by_host[HOST_A][1:3] == (newest_a - timedelta(minutes=60), newest_a)
  assert all(isinstance(c[0], str) for c in calls["scan"]), "literal params"
  assert all(c[3] == 60000 for c in calls["scan"])
  rec_b = client.doc(ic.host_key(HOST_B))
  assert rec_b["lag_vs_fleet_s"] == 4 * 3600 - 300
  assert rec_b["pair_count"] == 2
  assert rec_b["all_zero_pairs"] == [["host_cpu", "nice"]]
  sig_keys = [k for k in client.sets if k.startswith(ic.SIG_KEY_PREFIX)]
  assert len(sig_keys) == 1, "identical pair sets share one blob"
  assert client.doc(sig_keys[0])["pairs"] == [
    ["host_cpu", "nice"],
    ["host_cpu", "user"],
  ]
  assert client.ttl[ic.host_key(HOST_A)] == 3 * 3600
  assert client.ttl[sig_keys[0]] == 3 * 3600
  assert client.ttl[ic.SWEEP_KEY] == 3 * 3600
  assert beat["hosts_done"] == 2 and beat["incomplete"] == 0


def test_ingest_coverage_sweeper_timeout_marks_host_incomplete_and_continues():
  client = FakeValkey()

  def scan(host):
    if host == HOST_A:
      raise RuntimeError("canceling statement due to statement timeout")
    return [("host_cpu", "user", 1, True)]

  beat, _ = _sweep(
    client,
    [HOST_A, HOST_B],
    {HOST_A: _dt(NOW - 60), HOST_B: _dt(NOW - 60)},
    scan=scan,
  )
  rec_a = client.doc(ic.host_key(HOST_A))
  assert rec_a["status"] == ic.STATUS_INCOMPLETE
  assert "statement timeout" in rec_a["error"]
  assert "sig" not in rec_a, "a failed scan never looks like 'no pairs'"
  assert client.doc(ic.host_key(HOST_B))["pair_count"] == 1
  assert beat["incomplete"] == 1 and beat["hosts_done"] == 2


def test_ingest_coverage_sweeper_seven_day_bound_and_no_recent_data():
  client = FakeValkey()
  edge = _dt(NOW) - timedelta(days=7) + timedelta(minutes=10)
  beat, calls = _sweep(client, [HOST_A, HOST_B], {HOST_A: edge})
  since = calls["latest"][0][1]
  scanned = {c[0]: c for c in calls["scan"]}
  assert HOST_B not in scanned, "no row in 7 days -> not scanned"
  assert scanned[HOST_A][1] == since, "window lower bound clamps to 7 days"
  assert all(c[1] >= since for c in calls["scan"])
  assert client.doc(ic.host_key(HOST_B))["status"] == ic.STATUS_NO_RECENT_DATA
  assert beat["no_recent_data"] == 1


def test_ingest_coverage_sweeper_overrun_resumes_unreached_hosts_first():
  client = FakeValkey()
  latest = {h: _dt(NOW - 60) for h in (HOST_A, HOST_B, HOST_C)}
  # 2000 s per clock tick: the first host fits, the interval ends after it.
  beat, calls = _sweep(
    client,
    [HOST_A, HOST_B, HOST_C],
    latest,
    now_fn=Clock(step=2000.0),
  )
  first = [c[0] for c in calls["scan"]]
  assert first == [HOST_A]
  assert beat["hosts_done"] == 1 and beat["hosts_total"] == 3
  _beat, calls = _sweep(
    client, [HOST_A, HOST_B, HOST_C], latest, now_fn=Clock(start=NOW + 9000)
  )
  assert [c[0] for c in calls["scan"]] == [HOST_B, HOST_C, HOST_A]


def test_ingest_coverage_sweeper_heartbeat_start_and_end():
  client = FakeValkey()
  seen = []
  real_set = client.set

  def spy(key, value, ex=None):
    if key == ic.SWEEP_KEY:
      seen.append(json.loads(value))
    real_set(key, value, ex)

  client.set = spy
  _sweep(client, [HOST_A], {HOST_A: _dt(NOW - 60)})
  assert seen[0]["started_at"] == int(NOW) and seen[0]["hosts_done"] == 0
  assert seen[-1]["finished_at"] >= seen[-1]["started_at"]
  assert seen[-1]["hosts_done"] == 1


def test_ingest_coverage_sweeper_empty_inventory_writes_no_host_keys():
  client = FakeValkey()
  beat, calls = _sweep(client, [], {})
  assert beat["last_error"] == "empty recent_host inventory"
  assert calls["latest"] == [] and calls["scan"] == []
  assert [k for k in client.sets if k != ic.SWEEP_KEY] == []


ROT_AT = int(NOW) - 4 * 3600


@pytest.mark.parametrize(
  ("rotations", "newest", "previous", "status"),
  [
    # 4 h open segment, closed file fully ingested -> current.
    ([(ROT_AT, ROT_AT - 10)], ROT_AT - 10, None, ic.STATUS_CURRENT),
    # Normal send lag: newest == closed last sample, minutes before rotated_at.
    ([(ROT_AT, ROT_AT - 300)], ROT_AT - 300, None, ic.STATUS_CURRENT),
    # Monitor buffered for hours: old sample times, late $, file ingested.
    ([(ROT_AT, ROT_AT - 6 * 3600)], ROT_AT - 6 * 3600, None, ic.STATUS_CURRENT),
    # 20 h open segment with backlog 0 is still current (no monitor_quiet).
    (
      [(int(NOW) - 20 * 3600, int(NOW) - 20 * 3600)],
      int(NOW) - 20 * 3600,
      None,
      ic.STATUS_CURRENT,
    ),
    # Backlog seen once -> not yet ingest_behind.
    ([(ROT_AT, ROT_AT - 10)], ROT_AT - 3600, None, ic.STATUS_UNKNOWN),
    # Backlog for the same closed file on two sweeps -> ingest_behind.
    (
      [(ROT_AT, ROT_AT - 10)],
      ROT_AT - 3600,
      {"closed_last_sample_at": ROT_AT - 10, "ingest_backlog_s": 3590},
      ic.STATUS_INGEST_BEHIND,
    ),
    # A newer closed file since the last sweep restarts the two-sweep check.
    (
      [(ROT_AT, ROT_AT - 10)],
      ROT_AT - 3600,
      {"closed_last_sample_at": ROT_AT - 7200, "ingest_backlog_s": 100},
      ic.STATUS_UNKNOWN,
    ),
    # No rotation on record -> unknown.
    ([], ROT_AT, None, ic.STATUS_UNKNOWN),
    # Only rotations with a failed tail read -> unknown.
    ([(ROT_AT, None)], ROT_AT, None, ic.STATUS_UNKNOWN),
  ],
)
def test_derive_host_status(rotations, newest, previous, status):
  entries = [
    {"rotated_at": r, "closed_last_sample_at": c} for r, c in rotations
  ]
  out = ic.derive_host_status(entries, newest, previous, NOW)
  assert out["status"] == status
  if rotations:
    assert out["open_segment_age_s"] == int(NOW) - rotations[0][0]


def test_derive_host_status_null_tail_falls_back_to_older_rotation():
  entries = [
    {"rotated_at": ROT_AT, "closed_last_sample_at": None},
    {"rotated_at": ROT_AT - 21600, "closed_last_sample_at": ROT_AT - 21610},
  ]
  out = ic.derive_host_status(entries, ROT_AT - 21610, None, NOW)
  assert out["closed_last_sample_at"] == ROT_AT - 21610
  assert out["closed_until"] == ROT_AT
  assert out["status"] == ic.STATUS_CURRENT


def test_derive_host_status_whole_second_compare():
  """Microseconds on host_data.time never create a false backlog."""
  entries = [{"rotated_at": ROT_AT, "closed_last_sample_at": ROT_AT - 10}]
  newest = _dt(ROT_AT - 10) + timedelta(microseconds=999_000)
  out = ic.derive_host_status(entries, newest, None, NOW)
  assert out["ingest_backlog_s"] == 0


def test_sweep_stores_status_from_rotation_record():
  client = FakeValkey()
  _rot(client, HOST_A, int(NOW) - 3600, int(NOW) - 3610)
  _sweep(client, [HOST_A], {HOST_A: _dt(NOW - 3610)})
  rec = client.doc(ic.host_key(HOST_A))
  assert rec["status"] == ic.STATUS_CURRENT
  assert rec["ingest_backlog_s"] == 0
  assert rec["closed_last_sample_at"] == int(NOW) - 3610


def test_ingest_coverage_ini_getters_fall_back_to_documented_defaults(
  monkeypatch,
):
  assert cfg.ini_registry_default("ingest_coverage_sweep_enabled") == "yes"
  assert cfg.ini_registry_default("ingest_coverage_sweep_interval_s") == "3600"
  assert cfg.ini_registry_default("ingest_coverage_window_minutes") == "60"
  assert cfg.ini_registry_default("ingest_coverage_query_timeout_ms") == "60000"
  monkeypatch.setattr(cfg, "_ensure_cfg_loaded", lambda: None)
  monkeypatch.setattr(
    cfg, "_pipeline_get", lambda opt, *a, **k: cfg.ini_registry_default(opt)
  )
  monkeypatch.setattr(
    cfg,
    "_pipeline_getint",
    lambda opt, *a, **k: int(cfg.ini_registry_default(opt)),
  )
  assert cfg.get_ingest_coverage_sweep_enabled() is True
  assert cfg.get_ingest_coverage_sweep_interval_s() == 3600
  assert cfg.get_ingest_coverage_window_minutes() == 60
  assert cfg.get_ingest_coverage_query_timeout_ms() == 60000
  monkeypatch.setattr(cfg, "_pipeline_get", lambda opt, *a, **k: "no")
  monkeypatch.setattr(cfg, "_pipeline_getint", lambda opt, *a, **k: 0)
  assert cfg.get_ingest_coverage_sweep_enabled() is False
  assert cfg.get_ingest_coverage_sweep_interval_s() == 60
  assert cfg.get_ingest_coverage_window_minutes() == 1
  assert cfg.get_ingest_coverage_query_timeout_ms() == 1000


def test_sweeper_disabled_exits_without_db_or_valkey(monkeypatch):
  monkeypatch.setattr(cfg, "get_ingest_coverage_sweep_enabled", lambda: False)

  def _forbidden(*a, **k):
    raise AssertionError("disabled sweeper touched Django / DB / Valkey")

  from hpcperfstats.dbload.lib import django_bootstrap
  from hpcperfstats.site.lib.machine import cache_utils

  monkeypatch.setattr(django_bootstrap, "ensure_django", _forbidden)
  monkeypatch.setattr(cache_utils, "get_valkey_client", _forbidden)
  monkeypatch.setattr(ic, "run_sweep", _forbidden)
  assert entry.main() == 0
