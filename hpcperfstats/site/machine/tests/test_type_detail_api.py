"""Regression tests for type_detail host_data scoping (time + hosts only; jid not in filters)."""

from datetime import datetime, timezone

import pytest


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
