"""Tests for ORM-based data access (post raw-SQL migration). Verifies that
refactored code paths use Django ORM and that helpers work.

These tests are written to avoid touching a real database so they can run in
isolated environments (no PostgreSQL server required).

"""
from django.test import SimpleTestCase


class TestORMHelpers(SimpleTestCase):
  """Test queryset_to_dataframe and that models are queryable.

    """

  def test_queryset_to_dataframe_empty(self):
    """queryset_to_dataframe(job_data.objects.none()) returns empty DataFrame.

        """
    from hpcperfstats.analysis.gen.utils import queryset_to_dataframe
    from hpcperfstats.site.machine.models import job_data

    qs = job_data.objects.none()
    df = queryset_to_dataframe(qs)
    self.assertEqual(len(df), 0)

  def test_jid_table_missing_job_no_raise(self):
    """jid_table with non-existent jid should not raise; should set empty attrs.

        """
    from hpcperfstats.analysis.gen import jid_table

    jt = jid_table.jid_table("_nonexistent_jid_12345_")
    self.assertEqual(jt.jid, "_nonexistent_jid_12345_")
    self.assertFalse(hasattr(jt, "conj"))
    self.assertEqual(jt.acct_host_list, [])
    self.assertEqual(jt.host_list, [])
    self.assertEqual(jt.schema, {})
