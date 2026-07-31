"""Tests for ORM-based data access (post raw-SQL migration). Verifies that
refactored code paths use Django ORM and that helpers work.

These tests are written to avoid touching a real database so they can run in
isolated environments (no PostgreSQL server required).

"""
import pytest
from django.test import SimpleTestCase

pytestmark = pytest.mark.machine_unit_mock


class TestORMHelpers(SimpleTestCase):
  """Test queryset_to_dataframe and that models are queryable.

    """

  def test_queryset_to_dataframe_empty(self):
    """queryset_to_dataframe(job_data.objects.none()) returns empty DataFrame.

        """
    from hpcperfstats.analysis.metrics.lib.gen.utils import queryset_to_dataframe
    from hpcperfstats.site.lib.machine.models import job_data

    qs = job_data.objects.none()
    df = queryset_to_dataframe(qs)
    self.assertEqual(len(df), 0)

  def test_jid_table_missing_job_no_raise(self):
    """jid_table with non-existent jid should not raise; should set empty attrs.

        """
    from hpcperfstats.analysis.metrics.lib.gen import jid_table

    jt = jid_table.jid_table("_nonexistent_jid_12345_")
    self.assertEqual(jt.jid, "_nonexistent_jid_12345_")
    self.assertFalse(hasattr(jt, "conj"))
    self.assertEqual(jt.acct_host_list, [])
    self.assertEqual(jt.host_list, [])
    self.assertEqual(jt.schema, {})

  def test_host_data_primary_key_contract(self):
    """host_data identity stays anchored on time; uniqueness includes ``dev``."""
    from hpcperfstats.site.lib.machine.models import host_data

    self.assertEqual(host_data._meta.pk.name, "time")
    self.assertIn(
        ("time", "host", "type", "event", "dev"),
        host_data._meta.unique_together,
    )
