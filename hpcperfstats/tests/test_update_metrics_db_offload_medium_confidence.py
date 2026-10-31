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
  # Live SQL probe disabled: always persisted metrics_distinct_time_count.
  job_data.objects.filter(jid=jid).update(metrics_distinct_time_count=7)
  assert get_live_distinct_time_count_for_jid(jid) == 7


@pytest.mark.django_db(transaction=True)
def test_jid_scoped_distinct_union_parity_jid_dev_and_both():
  """0038 UNION: jid-only, dev=j: only, and both must not double-count."""
  oracles.skip_unless_compose_db()
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    jid_scoped_distinct_host_time_count,
  )
  from hpcperfstats.site.lib.machine.models import host_data, job_data

  start = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
  end = start + timedelta(hours=1)
  host = "n-union.dboff.test"
  t0 = start + timedelta(minutes=5)
  t1 = start + timedelta(minutes=10)

  cases = (
    ("920010", "920010", "", 2),
    ("920011", None, "j:920011", 2),
    ("920012", "920012", "j:920012", 2),
  )
  for jid, row_jid, row_dev, expect in cases:
    host_data.objects.filter(host=host).delete()
    job_data.objects.filter(jid=jid).delete()
    oracles.seed_minimal_job(jid=jid, start=start, end=end, host_list=[host])
    for ts in (t0, t1):
      host_data.objects.create(
        jid=row_jid,
        host=host,
        time=ts,
        type="cpu_counter_metrics",
        event="APERF",
        dev=row_dev,
        unit="count",
        value=1.0,
      )
    assert jid_scoped_distinct_host_time_count(jid, start, end) == expect


@pytest.mark.django_db(transaction=True)
def test_jid_scoped_distinct_host_filter_parity():
  """0039: p_hosts restricts count; NULL hosts matches unscoped UNION."""
  oracles.skip_unless_compose_db()
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    jid_scoped_distinct_host_time_count,
  )
  from hpcperfstats.site.lib.machine.models import host_data, job_data

  start = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
  end = start + timedelta(hours=1)
  host_a = "n-hf-a.dboff.test"
  host_b = "n-hf-b.dboff.test"
  jid = "920020"
  t0 = start + timedelta(minutes=5)
  t1 = start + timedelta(minutes=10)
  host_data.objects.filter(host__in=[host_a, host_b]).delete()
  job_data.objects.filter(jid=jid).delete()
  oracles.seed_minimal_job(
    jid=jid, start=start, end=end, host_list=[host_a, host_b]
  )
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
  assert jid_scoped_distinct_host_time_count(jid, start, end) == 3
  assert jid_scoped_distinct_host_time_count(jid, start, end, hosts=None) == 3
  assert (
    jid_scoped_distinct_host_time_count(jid, start, end, hosts=[host_a]) == 2
  )
  assert (
    jid_scoped_distinct_host_time_count(jid, start, end, hosts=[host_b]) == 1
  )


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
