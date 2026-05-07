"""Regression coverage for anonymous ``/pub`` expansion-factor aggregates."""

from datetime import datetime, timedelta

import pytest
from django.utils import timezone as dj_tz

from hpcperfstats.site.machine.models import job_data, public_metrics_artifact
from hpcperfstats.site.machine.public_metrics_artifacts import (
    PUBLIC_EF_MONTH_DAILY,
    PUBLIC_EF_YEAR_WEEKLY,
    compute_scheduler_expansion_factor_seconds,
    decompress_public_payload,
    refresh_public_expansion_factor_artifacts,
)


@pytest.mark.django_db
def test_refresh_public_expansion_factor_artifacts_parallel_inline_pool():
  """Parallel path with a pool that runs workers in-process matches sequential stats."""
  submit = datetime(2024, 3, 1, tzinfo=dj_tz.utc)
  start = datetime(2024, 3, 1, 1, 0, 0, tzinfo=dj_tz.utc)
  end = datetime(2024, 3, 15, 2, 0, 0, tzinfo=dj_tz.utc)
  runtime = float((end - start).total_seconds())
  job_data.objects.create(
      jid="pub_ef_parallel_demo",
      submit_time=submit,
      start_time=start,
      end_time=end,
      runtime=runtime,
      ncores=4,
      username="demo-user",
      host_list=["n001.cluster.example"],
  )

  from hpcperfstats.site.machine.public_metrics_artifacts import (
      refresh_public_expansion_factor_artifacts_parallel,
  )

  class _InlinePool:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del chunksize
      for t in tasks:
        yield fn(t)

  sequential = refresh_public_expansion_factor_artifacts()
  public_metrics_artifact.objects.all().delete()

  parallel = refresh_public_expansion_factor_artifacts_parallel(_InlinePool())

  assert parallel["rebuilt_month_periods"] == sequential["rebuilt_month_periods"]
  assert parallel["rebuilt_year_periods"] == sequential["rebuilt_year_periods"]
  assert public_metrics_artifact.objects.filter(scope=PUBLIC_EF_MONTH_DAILY).exists()


@pytest.mark.django_db
def test_public_ef_period_worker_closes_connections_before_reconcile(monkeypatch):
  import django.db

  from hpcperfstats.site.machine import public_metrics_artifacts as pma

  close_calls = []
  monkeypatch.setattr(django.db.connections, "close_all", lambda: close_calls.append(1))

  def fake_month(ym):
    del ym
    assert close_calls == [1], "connections.close_all() must run before ORM reconcile"
    return {"rebuilt_month_periods": 0, "skipped_month_periods": 1}

  monkeypatch.setattr(pma, "_sync_reconcile_public_ef_month", fake_month)

  result = pma._public_ef_period_worker((pma._PUBLIC_EF_KIND_MONTH, "2025-06"))
  assert close_calls == [1]
  assert result == {"rebuilt_month_periods": 0, "skipped_month_periods": 1}


@pytest.mark.machine_unit_mock
def test_build_public_expansion_factor_histogram_json_item_shape():
  from hpcperfstats.site.machine.public_metrics_artifacts import EF_HIST_BIN_EDGES
  from hpcperfstats.site.machine.public_metrics_bokeh import (
      build_public_expansion_factor_histogram_json_item,
  )

  edges = list(EF_HIST_BIN_EDGES)
  counts = [0] * len(edges)
  counts[3] = 2
  counts[-1] = 1
  item = build_public_expansion_factor_histogram_json_item(
      period_key="2024-03",
      period_kind="unit subtitle",
      edges=edges,
      counts=counts,
  )
  assert item is not None
  assert "doc" in item
  assert "root_id" in item
  assert "target_id" in item
  assert "version" in item


@pytest.mark.machine_unit_mock
def test_compute_scheduler_expansion_factor_formula_and_guards():
  submit = datetime(2024, 6, 1, 12, 0, tzinfo=dj_tz.utc)
  start = datetime(2024, 6, 1, 13, 0, tzinfo=dj_tz.utc)
  runtime = 100.0
  ncores = 4
  qw = (start - submit).total_seconds()
  ef = compute_scheduler_expansion_factor_seconds(submit, start, runtime, ncores)
  assert ef == pytest.approx((qw + runtime) / (ncores * runtime))

  assert compute_scheduler_expansion_factor_seconds(submit, start, 0.0, ncores) is None
  assert compute_scheduler_expansion_factor_seconds(submit, start, runtime, 0) is None
  bad_submit = start + timedelta(hours=1)
  assert compute_scheduler_expansion_factor_seconds(bad_submit, start, runtime, ncores) is None


@pytest.mark.django_db
def test_refresh_public_expansion_factor_artifacts_builds_rows():
  submit = datetime(2024, 3, 1, tzinfo=dj_tz.utc)
  start = datetime(2024, 3, 1, 1, 0, 0, tzinfo=dj_tz.utc)
  end = datetime(2024, 3, 15, 2, 0, 0, tzinfo=dj_tz.utc)
  runtime = float((end - start).total_seconds())
  job_data.objects.create(
      jid="pub_ef_demo",
      submit_time=submit,
      start_time=start,
      end_time=end,
      runtime=runtime,
      ncores=4,
      username="demo-user",
      host_list=["n001.cluster.example"],
  )

  assert public_metrics_artifact.objects.count() == 0
  stats = refresh_public_expansion_factor_artifacts()
  assert stats["rebuilt_month_periods"] >= 1
  assert stats["rebuilt_year_periods"] >= 1

  march = public_metrics_artifact.objects.get(
      scope=PUBLIC_EF_MONTH_DAILY, period_key="2024-03"
  )
  assert march.input_fingerprint

  march_payload = decompress_public_payload(march)
  assert "bokeh_histogram_json_item" in march_payload
  assert isinstance(march_payload["bokeh_histogram_json_item"], dict)
  assert "doc" in march_payload["bokeh_histogram_json_item"]
  assert "root_id" in march_payload["bokeh_histogram_json_item"]

  year_row = public_metrics_artifact.objects.get(
      scope=PUBLIC_EF_YEAR_WEEKLY, period_key="2024"
  )
  assert year_row.input_fingerprint

  year_payload = decompress_public_payload(year_row)
  assert "bokeh_histogram_json_item" in year_payload
  assert isinstance(year_payload["bokeh_histogram_json_item"], dict)


@pytest.mark.django_db
def test_invalidate_job_plot_also_drops_touching_public_artifacts():
  from hpcperfstats.site.machine.cache_utils import invalidate_job_plot_cache_keys_for_jids

  submit = datetime(2024, 4, 1, tzinfo=dj_tz.utc)
  start = datetime(2024, 4, 5, tzinfo=dj_tz.utc)
  end = datetime(2024, 4, 10, tzinfo=dj_tz.utc)
  runtime = float((end - start).total_seconds())
  job_data.objects.create(
      jid="pub_ef_inv",
      submit_time=submit,
      start_time=start,
      end_time=end,
      runtime=runtime,
      ncores=2,
      username="demo-user",
      host_list=["n002.cluster.example"],
  )
  refresh_public_expansion_factor_artifacts()
  assert public_metrics_artifact.objects.exists()

  invalidate_job_plot_cache_keys_for_jids(["pub_ef_inv"])
  assert not public_metrics_artifact.objects.filter(
      scope=PUBLIC_EF_MONTH_DAILY, period_key="2024-04"
  ).exists()
