"""Admin Monitor ingest coverage: classify, group, liveness, soft-fail, API."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from hpcperfstats.dbload.lib import ingest_coverage as ic
from hpcperfstats.site.lib.machine import admin_monitor_ingest_coverage as cov

pytestmark = pytest.mark.django_db(databases=[])

NOW = 1_800_000_000
DAY = 86400
CPU = ("host_cpu", "user")
NICE = ("host_cpu", "nice")
IDLE = ("host_cpu", "idle")
GPU = ("nvidia_gpu", "gpu_util")
OPA = ("host_opa", "rx_bytes")


def _rec(host, pairs, status=ic.STATUS_CURRENT, **extra):
  sig = ic.pair_set_signature(pairs)
  return {
    "fqdn": host,
    "sig": sig,
    "status": status,
    "pair_count": len(pairs),
    "scanned_at": NOW - 60,
    "newest_sample": NOW - 300,
    "ingest_backlog_s": 0,
    "open_segment_age_s": 3600,
    "all_zero_pairs": [],
    **extra,
  }, (sig, [list(p) for p in sorted(pairs)])


def _fixture(hosts_pairs, statuses=None):
  records, sigs = {}, {}
  for host, pairs in hosts_pairs.items():
    rec, (sig, blob) = _rec(
      host, pairs, (statuses or {}).get(host, ic.STATUS_CURRENT)
    )
    records[host] = rec
    sigs[sig] = blob
  return records, sigs


CONSUMED = {
  CPU: {"metrics": NOW - DAY},
  NICE: {"type_detail": NOW - DAY},
  GPU: {"summary_plot": NOW - DAY},
  ("host_mem", "used"): {"job_detail": NOW - DAY},
  IDLE: {"type_detail": NOW - 2 * DAY, "type_detail_dropped": NOW - DAY},
}
BEAT = {
  "started_at": NOW - 600,
  "finished_at": NOW - 100,
  "interval_s": 3600,
  "hosts_done": 4,
  "hosts_total": 4,
  "window_minutes": 60,
}


def _payload(fqdns, records, sigs, **kw):
  opts = {
    "consumed": CONSUMED,
    "identities": {},
    "beat": BEAT,
    "enabled": True,
    "interval_s": 3600,
    "window_minutes": 60,
    "now": NOW,
  }
  opts.update(kw)
  return cov.build_ingest_coverage_payload(
    fqdns=fqdns, host_records=records, sig_pairs=sigs, **opts
  )


def test_ingest_coverage_classify_and_group():
  hosts = {
    "c101-001.x": {CPU, NICE, IDLE, OPA},
    "c101-002.x": {CPU, NICE, IDLE, OPA},
    "c101-003.x": {CPU, NICE, IDLE, OPA},
    "c102-001.x": {CPU, NICE, IDLE},  # odd build: no host_opa
  }
  records, sigs = _fixture(hosts)
  identities = {
    h: {"capability_slug": "slug-a", "schema_types": ["host_cpu", "host_opa"]}
    for h in hosts
  }
  p = _payload(sorted(hosts), records, sigs, identities=identities)
  assert p["sweeper_state"] == "ok" and not p["incomplete"]
  assert p["consumer_registry_empty"] is False
  assert [g["hosts"] for g in p["groups"]] == [3, 1]
  base, odd = p["groups"]
  assert base["is_baseline"] and base["baseline_share"] == 1.0
  classes = {(r["type"], r["event"]): r["class"] for r in base["pairs"]}
  assert classes == {
    CPU: "derived",
    NICE: "type_detail_only",
    IDLE: "type_detail_dropped",
    OPA: "unused",
  }
  assert base["gap_count"] == 2
  assert base["pairs"][0]["class"] in cov.GAP_CLASSES, "gaps sort first"
  assert base["slugs"] == {"slug-a": 3}
  assert base["host_prefixes"] == {"c101": 3}
  assert odd["baseline_share"] == 0.0
  diff = {(d["type"], d["event"]): d for d in odd["diff_vs_baseline"]}
  assert diff[OPA]["host_fraction"] == 0.0
  assert diff[OPA]["baseline_fraction"] == 1.0
  # Reverse direction: GPU and host_mem were read but are not sent; only
  # types the group sends are listed (no GPU flood on CPU-only hosts).
  assert base["consumed_not_ingested"] == []
  assert "Ingest vs Job Detail coverage" in p["markdown"]
  assert "`host_opa` / `rx_bytes`: unused" in p["markdown"]


def test_ingest_coverage_consumed_not_ingested_limited_to_sent_types():
  records, sigs = _fixture({"c1-1.x": {CPU}})
  p = _payload(["c1-1.x"], records, sigs)
  # idle was last dropped but an older job's panel kept it: still consumed.
  assert p["groups"][0]["consumed_not_ingested"] == [
    ["host_cpu", "idle"],
    ["host_cpu", "nice"],
  ]


def test_ingest_coverage_group_by_rack_shows_outliers():
  hosts = {
    "c101-001.x": {CPU, OPA},
    "c101-002.x": {CPU, OPA},
    "c101-003.x": {CPU},
  }
  records, sigs = _fixture(hosts)
  p = _payload(sorted(hosts), records, sigs, group_by="rack")
  assert p["group_by"] == "rack"
  (group,) = p["groups"]
  assert group["key"] == "c101" and group["hosts"] == 3
  frac = {(r["type"], r["event"]): r["host_fraction"] for r in group["pairs"]}
  assert frac[OPA] == pytest.approx(2 / 3, abs=1e-4)
  assert [o["fqdn"] for o in group["outlier_hosts"]] == ["c101-003.x"]
  assert group["outlier_hosts"][0]["missing_vs_group"] == [list(OPA)]


def test_ingest_coverage_list_caps(monkeypatch):
  monkeypatch.setattr(cov, "MAX_GROUPS", 2)
  monkeypatch.setattr(cov, "MAX_HOSTS_PER_GROUP", 1)
  hosts = {f"c{i}-001.x": {CPU, ("t", str(i))} for i in range(5)}
  records, sigs = _fixture(hosts)
  p = _payload(sorted(hosts), records, sigs)
  assert len(p["groups"]) == 2 and p["groups_total"] == 5
  assert all(len(g["host_rows"]) <= 1 for g in p["groups"])


def test_ingest_coverage_soft_fail():
  records, sigs = _fixture({"a-1.x": {CPU}})
  # Empty inventory.
  p = _payload([], {}, {})
  assert p["incomplete"] and p["groups"] == []
  assert any("recent_host" in e for e in p["errors"])
  # Empty registry: no gaps listed from missing consumption data.
  p = _payload(["a-1.x"], records, sigs, consumed={})
  assert p["consumer_registry_empty"] and p["groups"] == []
  # Expired consumption counts as empty too.
  p = _payload(
    ["a-1.x"], records, sigs, consumed={CPU: {"metrics": NOW - 30 * DAY}}
  )
  assert p["consumer_registry_empty"]
  # Missing / expired host records and per-host incomplete records.
  rec_bad, _ = _rec("b-1.x", set(), status=ic.STATUS_INCOMPLETE)
  rec_bad.pop("sig")
  p = _payload(["a-1.x", "b-1.x", "c-1.x"], {**records, "b-1.x": rec_bad}, sigs)
  assert p["incomplete"]
  assert p["status_counts"]["incomplete"] == 1
  assert p["status_counts"]["not_scanned"] == 1
  assert [g["hosts"] for g in p["groups"]] == [1], "only real scans grouped"
  # Sig blob expired: host not grouped, flagged, never "missing everything".
  p = _payload(["a-1.x"], records, {})
  assert p["incomplete"] and p["groups"] == []
  assert p["status_counts"]["not_scanned"] == 1


def test_ingest_coverage_ingest_behind_excluded_from_trends():
  hosts = {"a-1.x": {CPU, OPA}, "a-2.x": {CPU}}
  records, sigs = _fixture(hosts, {"a-2.x": ic.STATUS_INGEST_BEHIND})
  p = _payload(sorted(hosts), records, sigs)
  assert [g["hosts"] for g in p["groups"]] == [1]
  assert [r["fqdn"] for r in p["excluded_ingest_behind"]] == ["a-2.x"]
  assert p["status_counts"]["ingest_behind"] == 1


@pytest.mark.parametrize(
  ("beat", "enabled", "state"),
  [
    (BEAT, False, "sweep_disabled"),
    (None, True, "sweeper_stale"),
    (
      {"started_at": NOW - 9 * 3600, "finished_at": NOW - 8 * 3600},
      True,
      "sweeper_stale",
    ),
    ({"started_at": NOW - 60, "finished_at": NOW - 3600}, True, "sweeping"),
    (BEAT, True, "ok"),
  ],
)
def test_ingest_coverage_sweeper_liveness(beat, enabled, state):
  records, sigs = _fixture({"a-1.x": {CPU, OPA}})
  p = _payload(["a-1.x"], records, sigs, beat=beat, enabled=enabled)
  assert p["sweeper_state"] == state
  if state == "sweep_disabled":
    assert p["groups"] == [], "disabled never looks like 'nothing missing'"
  if state == "sweeper_stale":
    rows = [r for g in p["groups"] for r in g["host_rows"]]
    assert rows and all(r["stale"] for r in rows)
    assert all(r["scanned_at"] == NOW - 60 for r in rows)


class _FakeValkey:
  def __init__(self, data, hashes):
    self.data = data
    self.hashes = hashes

  def mget(self, keys):
    return [self.data.get(k) for k in keys]

  def get(self, key):
    return self.data.get(key)

  def hgetall(self, key):
    return self.hashes.get(key, {})


def test_compute_ingest_coverage_reads_valkey_only(monkeypatch):
  records, sigs = _fixture({"c1-1.x": {CPU, OPA}})
  data = {ic.host_key(h): json.dumps(r) for h, r in records.items()}
  data.update(
    {ic.sig_key(s): json.dumps({"pairs": b}) for s, b in sigs.items()}
  )
  data[ic.SWEEP_KEY] = json.dumps({**BEAT, "finished_at": NOW - 10})
  data["monitor_identity:c1-1.x"] = json.dumps({"capability_slug": "s"})
  hashes = {"metrics_consumed:v1:metrics": {b"host_cpu\tuser": str(NOW)}}
  monkeypatch.setattr(
    cov, "get_valkey_client", lambda: _FakeValkey(data, hashes)
  )
  monkeypatch.setattr(
    cov, "list_recent_host_fqdns_from_valkey", lambda: ["c1-1.x"]
  )
  monkeypatch.setattr(cov.time, "time", lambda: NOW)
  monkeypatch.setattr(
    cov.cfg, "get_ingest_coverage_sweep_enabled", lambda: True
  )

  def _no_db(*a, **k):
    raise AssertionError("the coverage section must not touch the database")

  monkeypatch.setattr("django.db.backends.utils.CursorWrapper.execute", _no_db)
  p = cov.compute_ingest_coverage(force_refresh=True)
  assert p["sweeper_state"] == "ok"
  (group,) = p["groups"]
  assert group["slugs"] == {"s": 1}
  classes = {(r["type"], r["event"]): r["class"] for r in group["pairs"]}
  assert classes == {CPU: "derived", OPA: "unused"}


def test_compute_ingest_coverage_without_valkey_is_incomplete(monkeypatch):
  monkeypatch.setattr(cov, "get_valkey_client", lambda: None)
  p = cov.compute_ingest_coverage(force_refresh=True, group_by="slug")
  assert p["incomplete"] and p["groups"] == []
  assert any("Valkey unavailable" in e for e in p["errors"])


def _staff_request(params):
  request = RequestFactory().get("/api/admin_monitor/", params)
  request.session = {"is_staff": True}
  return request


def test_admin_monitor_ingest_coverage_section():
  from hpcperfstats.site.lib.machine import api

  payload = {"sweeper_state": "ok", "groups": []}
  with (
    patch.object(api, "_require_staff", return_value=None),
    patch.object(
      api, "compute_ingest_coverage", return_value=payload
    ) as compute,
  ):
    response = api.admin_monitor(
      _staff_request({"section": "ingest_coverage", "group_by": "rack"})
    )
  assert response.status_code == 200
  assert response.data == {"ingest_coverage": payload}
  compute.assert_called_once_with(force_refresh=False, group_by="rack")


def test_admin_monitor_ingest_coverage_refresh_clears_every_mode():
  from hpcperfstats.site.lib.machine import api

  with (
    patch.object(api, "_require_staff", return_value=None),
    patch.object(api, "compute_ingest_coverage", return_value={}),
    patch.object(api, "cache") as mock_cache,
  ):
    api.admin_monitor(
      _staff_request({"section": "ingest_coverage", "refresh": "1"})
    )
  deleted = {c.args[0] for c in mock_cache.delete.call_args_list}
  assert deleted == set(cov.all_cache_keys())


def test_admin_monitor_ingest_coverage_staff_only():
  from rest_framework.response import Response

  from hpcperfstats.site.lib.machine import api

  denied = Response({"detail": "staff only"}, status=403)
  with (
    patch.object(api, "_require_staff", return_value=denied),
    patch.object(api, "compute_ingest_coverage") as compute,
  ):
    response = api.admin_monitor(_staff_request({"section": "ingest_coverage"}))
  assert response.status_code == 403
  compute.assert_not_called()


def test_admin_monitor_full_payload_omits_ingest_coverage():
  from hpcperfstats.site.lib.machine import api

  with (
    patch.object(api, "_require_staff", return_value=None),
    patch.object(api, "cached_orm", return_value=[]),
    patch.object(api, "_get_recent_rabbitmq_host_stats", return_value=[]),
    patch.object(api, "_get_cache_stats", return_value={}),
    patch.object(api, "_get_rabbitmq_stats", return_value={}),
    patch.object(api, "_get_timescaledb_stats", return_value={}),
    patch.object(api, "_get_xalt_jid_coverage", return_value={}),
    patch.object(api, "compute_telemetry_health", return_value={}),
    patch.object(api, "compute_ingest_coverage") as compute,
  ):
    response = api.admin_monitor(_staff_request({}))
  assert "ingest_coverage" not in response.data
  compute.assert_not_called()
