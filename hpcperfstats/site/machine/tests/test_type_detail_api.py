"""Regression tests for type_detail host_data scoping (time + hosts only; jid not in filters)."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from hpcperfstats.analysis.gen.jid_table import JID_TABLE_HOST_QUERY_BATCH


@pytest.mark.django_db(databases=[])
def test_type_detail_host_data_filter_sql_has_no_jid_in_where_clause():
  """Type-detail host_data queries must not filter on host_data.jid."""
  from hpcperfstats.analysis.gen.jid_table import TypeDetailDataProvider

  st = datetime(2024, 1, 1, tzinfo=timezone.utc)
  et = datetime(2024, 1, 2, tzinfo=timezone.utc)
  provider = TypeDetailDataProvider(
      jid="job123",
      type_name="cpu",
      start_time=st,
      end_time=et,
      host_list=["n1.example.com"],
  )
  qs = provider._qs()
  sql = str(qs.query).lower()
  # jid may appear in the SELECT list, but not in WHERE (filtering).
  where_idx = sql.find(" where ")
  where_sql = sql[where_idx:] if where_idx != -1 else ""
  assert ".jid" not in where_sql


@pytest.mark.django_db(databases=[])
def test_type_detail_get_aggregate_df_batches_large_host_list():
  """TypeDetailDataProvider runs bounded host__in when host_list exceeds batch size."""
  from hpcperfstats.analysis.gen.jid_table import TypeDetailDataProvider

  st = datetime(2024, 1, 1, tzinfo=timezone.utc)
  et = datetime(2024, 1, 2, tzinfo=timezone.utc)
  n = JID_TABLE_HOST_QUERY_BATCH + 1
  hosts = ["n{0}.example.com".format(i) for i in range(n)]
  provider = TypeDetailDataProvider(
      jid="job123",
      type_name="cpu",
      start_time=st,
      end_time=et,
      host_list=hosts,
  )
  chunk_sizes = []

  class Qs:
    def values(self, *cols):
      return self

    def __iter__(self):
      return iter(())

  class Mgr:
    def filter(self, **kwargs):
      chunk_sizes.append(len(kwargs.get("host__in") or []))
      return Qs()

  with patch(
      "hpcperfstats.analysis.gen.jid_table.host_data.objects",
      Mgr(),
  ):
    with patch(
        "hpcperfstats.analysis.gen.jid_table.cached_orm",
        lambda _k, _ttl, fn: fn(),
    ):
      provider.get_aggregate_df("some_event", metric="arc")
  assert len(chunk_sizes) == 2
  assert chunk_sizes[0] == JID_TABLE_HOST_QUERY_BATCH
  assert chunk_sizes[1] == 1
