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
def test_large_job_time_slot_copy_keeps_every_host_per_slot():
  """Thinned COPY (time__slots) returns each host's own sample in every slot.

  The old strided axis kept one cross-host MAX(time) per bucket, so
  ``time = ANY`` matched about one host per bucket (hs04 COPY time lists).
  """
  oracles.skip_unless_compose_db()
  from hpcperfstats.analysis.metrics.lib.columnar import copy_fetch
  from hpcperfstats.site.lib.machine.models import host_data

  jid = "920002"
  hosts = ["n-slot-a.dboff.test", "n-slot-b.dboff.test"]
  start = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
  host_data.objects.filter(host__in=hosts).delete()
  rows = []
  for minute in range(120):
    for k, h in enumerate(hosts):
      # Hosts sample at different sub-minute offsets, like production.
      t = start + timedelta(minutes=minute, seconds=7 + 31 * k)
      rows.append(
        host_data(
          jid=jid,
          host=h,
          time=t,
          type="cpu",
          event="user",
          dev="0",
          unit="cs",
          value=float(minute),
        )
      )
  host_data.objects.bulk_create(rows)
  slots = [start + timedelta(minutes=10 * i) for i in range(12)]
  sql, params = copy_fetch.build_copy_sql(
    hosts, {"time__slots": slots, "time__slot_s": 60}, jid, shared=False
  )
  inner = sql.removeprefix("COPY (").removesuffix(") TO STDOUT (FORMAT BINARY)")
  from django.db import connection

  with connection.cursor() as cur:
    cur.execute(f"SELECT host, COUNT(*) FROM ({inner}) s GROUP BY 1", params)
    per_host = dict(cur.fetchall())
  assert per_host == {hosts[0]: 12, hosts[1]: 12}
  host_data.objects.filter(host__in=hosts).delete()


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


@pytest.mark.django_db(transaction=True)
def test_jid_table_index_probes_match_full_scan_oracle():
  """Probe rewrites (hs04 jid_table / COPY timeouts) equal the full scans."""
  oracles.skip_unless_compose_db()
  from django.db.models import Max, Min, Q

  from hpcperfstats.analysis.metrics.lib.columnar import copy_fetch
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import (
    first_last_scoped_times,
    hosts_owning_mid_sample,
    rows_per_sample_estimate,
    schema_rows_near_bounds,
  )
  from hpcperfstats.site.lib.machine.models import host_data

  own, shared, idle = (f"n-{x}.probe.test" for x in ("own", "shr", "idle"))
  tail = "n-tail.probe.test"
  jid = "930777"
  start = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
  end = start + timedelta(hours=3)
  host_data.objects.filter(host__in=[own, shared, idle, tail]).delete()
  rows = []
  for minute in range(0, 180, 1):
    t = start + timedelta(minutes=minute)
    # hs04 job 71526: job rows stop long before the window end.
    rows.append(
      (tail, jid if 5 <= minute < 120 else None, "cpu", "user", "0", t)
    )
    rows.append((own, jid, "cpu", "user", "0", t))
    rows.append((own, jid, "cpu", "sys", "0", t))
    rows.append((shared, None, "cpu", "user", f"j:{jid}", t))
    rows.append((shared, "1", "cpu", "user", "j:1", t))
    rows.append((idle, "2", "cpu", "user", "0", t))
  # Slow key only in the boundary (first) sample, like a monitor full collect.
  rows.append((own, jid, "host_mem", "used", "", start))
  host_data.objects.bulk_create(
    host_data(
      host=h, jid=j, type=ty, event=ev, dev=d, unit="u", time=t, value=1
    )
    for h, j, ty, ev, d, t in rows
  )
  scope = Q(jid=jid) | Q(dev=f"j:{jid}")
  hosts = [own, shared, idle]
  bounds = first_last_scoped_times(hosts, start, end, jid)
  for h in hosts:
    agg = host_data.objects.filter(
      scope, host=h, time__gte=start, time__lte=end
    )
    oracle = agg.aggregate(mn=Min("time"), mx=Max("time"))
    assert bounds[h] == (oracle["mn"], oracle["mx"])
  assert bounds[idle] == (None, None)
  assert first_last_scoped_times([tail], start, end, jid)[tail] == (
    start + timedelta(minutes=5),
    start + timedelta(minutes=119),
  )
  # Midpoint ownership (hs04: job 71526 accounting hosts without job rows
  # made "first owned row" probes read the whole window): owned and shared
  # (j: stamp) hosts count, a host with only other jobs' rows does not.
  assert hosts_owning_mid_sample(hosts, start, end, jid) == {own, shared}
  full = set(
    host_data.objects.filter(scope, host=own, time__gte=start, time__lte=end)
    .values_list("type", "event", "unit")
    .distinct()
  )
  first, last = bounds[own]
  assert set(schema_rows_near_bounds(own, first, last, 600, jid)) == full
  assert rows_per_sample_estimate([own, shared], start, end) == 2
  sql, params = copy_fetch.build_copy_sql(
    hosts, {"time__gte": start, "time__lte": end}, jid, shared=False
  )
  inner = sql.removeprefix("COPY (").removesuffix(") TO STDOUT (FORMAT BINARY)")
  from django.db import connection

  with connection.cursor() as cur:
    cur.execute(f"SELECT COUNT(*) FROM ({inner}) s", params)
    n_copy = cur.fetchone()[0]
  n_orm = host_data.objects.filter(
    scope, host__in=hosts, time__gte=start, time__lte=end
  ).count()
  assert n_copy == n_orm == 180 * 3 + 1
  host_data.objects.filter(host__in=hosts).delete()
