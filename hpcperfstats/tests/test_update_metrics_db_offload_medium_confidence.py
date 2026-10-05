"""Compose-DB oracles for metrics offloads #6-#11 (Phase T gate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hpcperfstats.tests import db_offload_oracles as oracles


@pytest.mark.django_db(transaction=True)
def test_live_distinct_host_time_count_compose_seed():
  oracles.skip_unless_compose_db()
  from hpcperfstats.analysis.metrics.lib.live_host_sample_count import (
    LiveJidScopedDistinctHostTimeCount,
  )
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    jid_scoped_distinct_host_time_count,
  )
  from hpcperfstats.site.lib.machine.job_plot_artifacts import (
    get_live_distinct_time_count_for_jid,
  )
  from hpcperfstats.site.lib.machine.models import host_data, job_data

  suffix = ".dboff.test"
  host_a = f"n-a{suffix}"
  host_b = f"n-b{suffix}"
  start = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
  end = start + timedelta(hours=2)
  jid = "920001"

  host_data.objects.filter(jid=jid).delete()
  job_data.objects.filter(jid=jid).delete()

  oracles.seed_minimal_job(
    jid=jid, start=start, end=end, host_list=[host_a, host_b]
  )
  t0 = start + timedelta(minutes=5)
  t1 = start + timedelta(minutes=10)
  for host, ts in ((host_a, t0), (host_a, t1), (host_b, t0)):
    host_data.objects.create(
      jid=jid,
      host=host,
      time=ts,
      type="cpu_counter_metrics",
      event="APERF",
      dev="",
      unit="count",
      value=1.0,
    )

  expr = LiveJidScopedDistinctHostTimeCount(suffix)
  qs = job_data.objects.filter(jid=jid).annotate(live_distinct=expr)
  row = qs.get()
  assert int(row.live_distinct) == 3
  assert jid_scoped_distinct_host_time_count(jid, start, end) == 3
  assert get_live_distinct_time_count_for_jid(jid) == 3


@pytest.mark.django_db(transaction=True)
def test_strided_distinct_times_db_function_matches_grouped_max_sql():
  oracles.skip_unless_compose_db()
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    strided_bucket_max_times,
  )
  from hpcperfstats.site.lib.machine.models import host_data, job_data

  jid = "920002"
  host = "n-stride.dboff.test"
  start = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
  end = start + timedelta(hours=1)
  host_data.objects.filter(jid=jid).delete()
  job_data.objects.filter(jid=jid).delete()
  oracles.seed_minimal_job(jid=jid, start=start, end=end, host_list=[host])
  base = int(start.timestamp())
  for i in range(20):
    host_data.objects.create(
      jid=jid,
      host=host,
      time=datetime.fromtimestamp(base + i * 60, tz=UTC),
      type="cpu_counter_metrics",
      event="APERF",
      dev="",
      unit="count",
      value=float(i),
    )
  nb = 8
  via_fn = strided_bucket_max_times([host], start, end, nb)
  distinct = jt_mod._distinct_times_in_window_batched(start, end, [host])
  span = (end - start).total_seconds()
  step = max(span / float(max(nb - 1, 1)), 1e-9)
  expected = jt_mod._date_bin_bucket_maxima(distinct, start, step)
  assert via_fn
  assert via_fn == expected


@pytest.mark.django_db(transaction=True)
def test_in_window_per_host_bounds_sql_matches_orm_oracle():
  oracles.skip_unless_compose_db()
  from datetime import UTC, datetime, timedelta

  from django.db.models import Max, Min

  from hpcperfstats.analysis.metrics import update_metrics as um
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    in_window_per_host_min_max_rows,
  )
  from hpcperfstats.site.lib.machine.models import host_data

  host = "n-bounds.dboff.test"
  start = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
  end = start + timedelta(hours=2)
  host_data.objects.filter(host=host).delete()
  for minute in (5, 10, 55):
    host_data.objects.create(
      jid="930001",
      host=host,
      time=start + timedelta(minutes=minute),
      type="cpu_counter_metrics",
      event="APERF",
      dev="",
      unit="count",
      value=float(minute),
    )
  tkw = {"time__gte": start, "time__lte": end}
  sql_rows = {
    r["host"]: (r["mn"], r["mx"])
    for r in in_window_per_host_min_max_rows([host], tkw)
  }
  orm_rows = list(
    host_data.objects.filter(host=host, **tkw)
    .values("host")
    .annotate(mn=Min("time"), mx=Max("time"))
  )
  assert len(orm_rows) == 1
  assert sql_rows[host][0] == orm_rows[0]["mn"]
  assert sql_rows[host][1] == orm_rows[0]["mx"]
  host_min, host_max = um._in_window_per_host_bounds([host], start, end)
  assert host_min[host] == orm_rows[0]["mn"]
  assert host_max[host] == orm_rows[0]["mx"]


@pytest.mark.django_db(databases=[])
def test_compute_metrics_skips_full_host_data_df_without_complex_metrics(
  monkeypatch,
):
  """#7: full pivot DF loads only when complex_metrics_list is non-empty."""
  from types import SimpleNamespace

  from hpcperfstats.analysis.metrics.lib import metrics as metrics_mod
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod

  calls = {"n": 0}

  def _track_get_full_host_data_df(self, *a, **k):
    calls["n"] += 1
    import pandas as pd

    return pd.DataFrame(
      columns=["host", "time", "type", "event", "value", "arc"]
    )

  monkeypatch.setattr(
    jt_mod.jid_table, "get_full_host_data_df", _track_get_full_host_data_df
  )

  class _JtCtx:
    def __enter__(self):
      jt = SimpleNamespace(
        jid="1",
        schema={},
        _base_filter={"host__in": ["h.example.org"]},
      )
      jt._host_data_qs = lambda **k: SimpleNamespace(exists=lambda: True)
      return jt

    def __exit__(self, *a):
      return False

  monkeypatch.setattr(jt_mod, "jid_table", lambda _jid: _JtCtx())
  monkeypatch.setattr(
    metrics_mod,
    "get_live_distinct_time_count_for_jid",
    lambda _jid: 0,
    raising=False,
  )
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_plot_artifacts.get_live_distinct_time_count_for_jid",
    lambda _jid: 0,
  )

  job = SimpleNamespace(
    jid="1",
    telemetry_first_time=None,
    telemetry_last_time=None,
    host_data_schema_json=None,
    save=lambda **k: None,
  )
  monkeypatch.setattr(
    metrics_mod,
    "_in_window_telemetry_bounds_for_job",
    lambda _j: (None, None),
  )
  monkeypatch.setattr(
    metrics_mod,
    "compute_job_detail_fsio_metric_rows",
    lambda _jt: [],
  )
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary.compute_job_gpu_summary_tuple",
    lambda _jt: (None, None, None, None),
  )

  m = metrics_mod.Metrics()
  m.complex_metrics_list = []
  m.simple_metrics_list = {}
  m.compute_metrics(job)
  assert calls["n"] == 0


@pytest.mark.django_db(transaction=True)
def test_job_arc_sql_cluster_oracle():
  oracles.skip_unless_compose_db()
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    job_arc_cluster_aggregate_value,
  )
  from hpcperfstats.site.lib.machine.models import host_data, job_data

  host = "n-arc.dboff.test"
  start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
  end = start + timedelta(hours=1)
  jid = "940001"
  host_data.objects.filter(jid=jid).delete()
  job_data.objects.filter(jid=jid).delete()
  oracles.seed_minimal_job(jid=jid, start=start, end=end, host_list=[host])
  # Two buckets: first should drop; second kept.
  t_first = start + timedelta(minutes=6)
  t_second = start + timedelta(minutes=16)
  for ts, arc in ((t_first, 10.0), (t_second, 20.0)):
    host_data.objects.create(
      jid=jid,
      host=host,
      time=ts,
      type="cpu_counter_metrics",
      event="APERF",
      dev="",
      unit="count",
      arc=arc,
      value=0.0,
    )
  tkw = {"time__gte": start, "time__lte": end}
  sql_val = job_arc_cluster_aggregate_value(
    [host],
    tkw,
    "cpu_counter_metrics",
    ["APERF"],
    1.0,
    host_aggregate="mean",
  )
  assert sql_val == 20.0


@pytest.mark.django_db(transaction=True)
def test_host_data_sum_metric_db_function_matches_orm():
  oracles.skip_unless_compose_db()
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    host_data_sum_metric_per_sample_rows,
  )
  from hpcperfstats.site.lib.machine.models import host_data

  host = "n-sum.dboff.test"
  start = datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
  end = start + timedelta(minutes=30)
  host_data.objects.filter(host=host).delete()
  ts = start + timedelta(minutes=5)
  host_data.objects.create(
    jid="950001",
    host=host,
    time=ts,
    type="host_cpu",
    event="user",
    dev="gpu0",
    unit="percent",
    arc=3.0,
    value=0.0,
  )
  host_data.objects.create(
    jid="950001",
    host=host,
    time=ts,
    type="host_cpu",
    event="user",
    dev="gpu1",
    unit="percent",
    arc=7.0,
    value=0.0,
  )
  tkw = {"time__gte": start, "time__lte": end}
  fn_rows = host_data_sum_metric_per_sample_rows(
    [host], tkw, "host_cpu", ["user"], "arc"
  )
  qs = host_data.objects.filter(
    host=host, **tkw, type="host_cpu", event__in=["user"]
  )
  orm_qs = jt_mod.host_data_sum_val_per_sample_queryset(qs, "arc")
  orm_rows = list(orm_qs.values("host", "sample_time", "sum_val"))
  assert len(fn_rows) == 1
  assert len(orm_rows) == 1
  assert fn_rows[0]["host"] == orm_rows[0]["host"]
  assert (
    abs(float(fn_rows[0]["sum_val"]) - float(orm_rows[0]["sum_val"])) < 1e-6
  )
