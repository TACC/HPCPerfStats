"""Tests for ORM-based data access (post raw-SQL migration). Verifies that refactored code paths use Django ORM and that helpers work. Run with: python manage.py test hpcperfstats.site.machine.tests.test_orm_migration (requires Django settings and config, e.g. hpcperfstats.ini).

AI generated.
"""
from django.test import TestCase


class TestORMHelpers(TestCase):
  """Test queryset_to_dataframe and that models are queryable.

    AI generated.
    """

  def test_queryset_to_dataframe_empty(self):
    """queryset_to_dataframe(job_data.objects.none()) returns empty DataFrame.

        AI generated.
        """
    from hpcperfstats.analysis.gen.utils import queryset_to_dataframe
    from hpcperfstats.site.machine.models import job_data

    qs = job_data.objects.none()
    df = queryset_to_dataframe(qs)
    self.assertEqual(len(df), 0)

  def test_jid_table_missing_job_no_raise(self):
    """jid_table with non-existent jid should not raise; should set empty attrs.

        AI generated.
        """
    from hpcperfstats.analysis.gen import jid_table

    jt = jid_table.jid_table("_nonexistent_jid_12345_")
    self.assertEqual(jt.jid, "_nonexistent_jid_12345_")
    self.assertIsNone(jt.conj)
    self.assertEqual(jt.acct_host_list, [])
    self.assertEqual(jt.host_list, [])
    self.assertEqual(jt.schema, {})
