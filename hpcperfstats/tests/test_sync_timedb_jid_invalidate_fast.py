"""Ingest-fast jid cache invalidation skips Redis wildcard SCAN."""
from __future__ import annotations

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.site.lib.machine import cache_utils


def test_invalidate_jid_caches_uses_ingest_fast(monkeypatch):
  calls = []

  def _derived(jids, *, ingest_fast=False):
    calls.append(("derived", ingest_fast, tuple(sorted(jids))))

  def _plots(jids, *, ingest_fast=False):
    calls.append(("plots", ingest_fast, tuple(sorted(jids))))

  monkeypatch.setattr(cache_utils, "invalidate_jid_derived_cache_keys", _derived)
  monkeypatch.setattr(
      cache_utils, "invalidate_job_plot_cache_keys_for_jids", _plots,
  )

  import pandas as pd

  stats = pd.DataFrame({"jid": ["100", "100", "101"]})
  st._invalidate_jid_caches(stats, None)

  assert ("derived", True, ("100", "101")) in calls
  assert ("plots", True, ("100", "101")) in calls


def test_plot_invalidate_ingest_fast_skips_wildcard_scan(monkeypatch):
  scan_calls = []

  class _Client:
    def smembers(self, _key):
      return set()

    def delete(self, _key):
      return 1

    def scan_iter(self, match=None, count=500):
      scan_calls.append((match, count))
      return iter(())

  monkeypatch.setattr(cache_utils, "_get_redis_py_client", lambda: _Client())
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.models.job_plot_artifact.objects.filter",
      lambda *a, **k: type("Q", (), {"delete": lambda self: 0})(),
      raising=False,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.models.job_detail_artifact.objects.filter",
      lambda *a, **k: type("Q", (), {"delete": lambda self: 0})(),
      raising=False,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.public_metrics_artifacts."
      "invalidate_public_metrics_artifacts_for_jids",
      lambda _j: None,
      raising=False,
  )

  cache_utils.invalidate_job_plot_cache_keys_for_jids(["j1"], ingest_fast=True)
  assert scan_calls == []


def test_plot_invalidate_default_still_scans_legacy_keys(monkeypatch):
  scan_calls = []

  class _Client:
    def smembers(self, _key):
      return set()

    def delete(self, _key):
      return 1

    def scan_iter(self, match=None, count=500):
      scan_calls.append(match)
      return iter(())

  monkeypatch.setattr(cache_utils, "_get_redis_py_client", lambda: _Client())
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.models.job_plot_artifact.objects.filter",
      lambda *a, **k: type("Q", (), {"delete": lambda self: 0})(),
      raising=False,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.models.job_detail_artifact.objects.filter",
      lambda *a, **k: type("Q", (), {"delete": lambda self: 0})(),
      raising=False,
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.public_metrics_artifacts."
      "invalidate_public_metrics_artifacts_for_jids",
      lambda _j: None,
      raising=False,
  )

  cache_utils.invalidate_job_plot_cache_keys_for_jids(["j1"], ingest_fast=False)
  assert len(scan_calls) == 2
