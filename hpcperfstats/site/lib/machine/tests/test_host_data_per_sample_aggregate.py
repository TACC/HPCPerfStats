"""PostgreSQL regression: per-(host, time) host_data sums stay per host.

``host_data.time`` is declared ``primary_key=True`` even though the table is
unique on (time, host, type, event, dev). PostgreSQL advertises
``allows_group_by_selected_pks``, so Django used to drop ``host`` from
``GROUP BY`` as functionally dependent and sum every host together at each
timestamp. Compiled-SQL coverage lives in ``test_jid_table.py``; these tests run
the aggregate against a real database.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    HOST_DATA_SUM_VAL_ALIAS,
    HOST_DATA_TIME_ALIAS,
    host_data_sum_val_per_sample_queryset,
)
from hpcperfstats.site.lib.machine.models import host_data


@pytest.mark.django_db
def test_per_sample_sum_is_per_host_on_postgresql():
  """Two hosts sampled at the same timestamps must not be summed together."""
  if connection.vendor != "postgresql":
    pytest.skip("GROUP BY functional dependency is PostgreSQL-specific")
  t0 = timezone.now().replace(microsecond=0) - timedelta(hours=3)
  t1 = t0 + timedelta(seconds=60)
  rows = []
  for host, scale in (("agg-h1.example.com", 1.0), ("agg-h2.example.com", 10.0)):
    for t_idx, sample_time in enumerate((t0, t1)):
      for event in ("user", "system"):
        rows.append(
            host_data(
                time=sample_time,
                host=host,
                jid="agg-jid",
                type="agg_host_cpu",
                dev="",
                event=event,
                unit="counter",
                value=1.0,
                delta=1.0,
                arc=scale * (1.0 if event == "user" else 2.0) + t_idx,
            )
        )
  host_data.objects.bulk_create(rows)

  qs = host_data_sum_val_per_sample_queryset(
      host_data.objects.filter(
          jid="agg-jid",
          type="agg_host_cpu",
          event__in=["user", "system"],
      ),
      "arc",
  )
  got = {
      (row["host"], row[HOST_DATA_TIME_ALIAS]): row[HOST_DATA_SUM_VAL_ALIAS]
      for row in qs
  }
  assert got == {
      ("agg-h1.example.com", t0): 3.0,
      ("agg-h1.example.com", t1): 5.0,
      ("agg-h2.example.com", t0): 30.0,
      ("agg-h2.example.com", t1): 32.0,
  }


@pytest.mark.django_db
def test_per_sample_sum_null_and_negative_semantics_on_postgresql():
  """All-dropped groups coalesce to 0.0; ``nonnegative_only`` drops negatives."""
  if connection.vendor != "postgresql":
    pytest.skip("PostgreSQL-only aggregate semantics")
  t0 = timezone.now().replace(microsecond=0) - timedelta(hours=4)
  host_data.objects.bulk_create([
      host_data(
          time=t0,
          host="agg-null.example.com",
          jid="agg-null-jid",
          type="agg_null_type",
          dev="",
          event="user",
          unit="counter",
          value=None,
          delta=None,
          arc=None,
      ),
      host_data(
          time=t0,
          host="agg-null.example.com",
          jid="agg-null-jid",
          type="agg_null_type",
          dev="d1",
          event="system",
          unit="counter",
          value=None,
          delta=None,
          arc=-5.0,
      ),
  ])
  base = host_data.objects.filter(jid="agg-null-jid", type="agg_null_type")

  plain = list(host_data_sum_val_per_sample_queryset(base, "arc"))
  assert [row[HOST_DATA_SUM_VAL_ALIAS] for row in plain] == [-5.0]

  nonneg = list(
      host_data_sum_val_per_sample_queryset(base, "arc", nonnegative_only=True))
  assert [row[HOST_DATA_SUM_VAL_ALIAS] for row in nonneg] == [0.0]

  raw_null = list(
      host_data_sum_val_per_sample_queryset(base, "value", coalesce_zero=False))
  assert [row[HOST_DATA_SUM_VAL_ALIAS] for row in raw_null] == [None]
