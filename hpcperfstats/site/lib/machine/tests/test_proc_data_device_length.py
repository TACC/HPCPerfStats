"""proc_data.device must hold the full monitor device token without a length cap."""

import re

import pytest

# name/pid/cmask/mmask with a 520-character Cpus_allowed_list: longer than the old
# varchar(512) column, which made the monitor cut the token at 511 bytes.
LONG_CMASK = ",".join(str(cpu) for cpu in range(0, 400, 2))[:520]
LONG_DEVICE = f"python3/123456/{LONG_CMASK}/0-1"


@pytest.mark.machine_unit_mock
def test_proc_data_device_has_no_max_length():
  """The model declares device as an unbounded varchar (max_length=None)."""
  from hpcperfstats.site.lib.machine.models import proc_data

  assert len(LONG_DEVICE) > 512
  assert proc_data._meta.get_field("device").max_length is None


@pytest.mark.machine_unit_mock
def test_proc_stage_ddl_device_is_unbounded():
  """The COPY staging table must not re-impose a device length cap."""
  from hpcperfstats.dbload.lib import sync_timedb_proc_data_insert as pdi

  device_line = re.search(r"^\s*device\s+(\S+)", pdi._STAGE_DDL, re.MULTILINE)
  assert device_line is not None
  assert device_line.group(1) == "varchar"


def test_proc_data_bulk_create_persists_long_device():
  """Postgres stores a device token longer than 512 characters unchanged."""
  from hpcperfstats.site.lib.machine.models import proc_data

  proc_data.objects.bulk_create(
    [proc_data(jid="9001", host="c001-001", proc="python3", device=LONG_DEVICE)]
  )
  row = proc_data.objects.get(jid="9001", host="c001-001", proc="python3")
  assert row.device == LONG_DEVICE


@pytest.mark.machine_unit_mock
def test_0046_alters_only_proc_data_device_to_unbounded():
  """0046 is one AlterField: proc_data.device with max_length=None."""
  import importlib

  mod = importlib.import_module(
    "hpcperfstats.site.lib.machine.migrations.0046_proc_data_device_unbounded"
  )
  (op,) = mod.Migration.operations
  assert (op.model_name, op.name) == ("proc_data", "device")
  assert op.field.max_length is None
  assert mod.Migration.dependencies == [
    ("machine", "0045_drop_duplicate_indexes")
  ]


def test_0046_sqlmigrate_is_type_only():
  """sqlmigrate widens proc_data.device with no USING cast and no other table."""
  from io import StringIO

  from django.core.management import call_command

  out = StringIO()
  call_command("sqlmigrate", "machine", "0046", stdout=out)
  sql = out.getvalue()
  assert 'ALTER COLUMN "device" TYPE varchar' in sql
  assert "varchar(" not in sql
  assert " USING " not in sql
  assert "host_data" not in sql


def test_0046_applied_column_is_unbounded_varchar():
  """After migrate, the live column is character varying with no length."""
  from django.db import connection

  with connection.cursor() as cursor:
    cursor.execute(
      "SELECT data_type, character_maximum_length FROM information_schema.columns"
      " WHERE table_name = 'proc_data' AND column_name = 'device'"
    )
    assert cursor.fetchone() == ("character varying", None)
