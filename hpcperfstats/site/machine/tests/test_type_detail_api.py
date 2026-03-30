"""Regression tests for type_detail host_data jid scoping (no live DB required)."""
import django
import pytest

pytestmark = pytest.mark.django_db(databases=[])


def test_type_detail_jid_filter_sql_includes_null_and_empty_jid():
  """type_detail_host_data_jid_q must OR exact jid with NULL and empty string."""
  django.setup()
  from hpcperfstats.analysis.gen.jid_table import type_detail_host_data_jid_q
  from hpcperfstats.site.machine.models import host_data

  qs = host_data.objects.filter(
      type_detail_host_data_jid_q("job123"),
      type="nvidia_gpu",
  )
  sql = str(qs.query).upper()
  assert "IS NULL" in sql
  assert " OR " in sql or "OR" in sql
