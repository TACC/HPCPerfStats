"""job_data.jid must store heterogeneous sacct JobIDs longer than 32 characters."""

import pytest
from django.utils import timezone

LOGGED_JID_35 = "2765795_[173-193,195-196,198-199%1]"
LOGGED_JID_36 = "2768288_[175,179,182-183,185,191%10]"


@pytest.mark.machine_unit_mock
def test_job_data_jid_max_length_fits_logged_heterogeneous_ids():
  """Logged heterogeneous JobIDs fit the field and a 513-character id does not."""
  from hpcperfstats.site.lib.machine.models import job_data

  field = job_data._meta.get_field("jid")
  assert field.max_length == 512
  assert len(LOGGED_JID_35) > 32
  assert len(LOGGED_JID_36) > 32
  assert len(LOGGED_JID_35) <= field.max_length
  assert len(LOGGED_JID_36) <= field.max_length
  assert field.max_length < 513


def test_job_data_bulk_create_persists_logged_36_char_jid():
  """Postgres accepts the logged 36-character jid that varchar(32) rejected."""
  from hpcperfstats.site.lib.machine.models import job_data

  jid = LOGGED_JID_36
  assert len(jid) > 32
  now = timezone.now()
  job_data.objects.bulk_create(
    [
      job_data(
        jid=jid,
        submit_time=now,
        start_time=now,
        end_time=now,
        username="acct",
        host_list=["n1"],
      )
    ]
  )
  assert job_data.objects.get(pk=jid).jid == jid


def test_0035_sqlmigrate_is_typmod_only():
  """sqlmigrate widens job_data.jid with no USING cast and no hypertable DDL."""
  from io import StringIO

  from django.core.management import call_command

  out = StringIO()
  call_command("sqlmigrate", "machine", "0035", stdout=out)
  sql = out.getvalue()
  assert "varchar(512)" in sql
  assert " USING " not in sql
  assert "host_data" not in sql
  assert "proc_data" not in sql
