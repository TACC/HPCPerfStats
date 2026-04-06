"""Regression tests for migration SQL/contracts that are easy to break silently."""
import importlib

from django.db import migrations


def test_0002_statsro_create_role_is_duplicate_safe():
  mod = importlib.import_module("hpcperfstats.site.machine.migrations.0002_add_read_only_user")
  run_sql_ops = [op for op in mod.Migration.operations if isinstance(op, migrations.RunSQL)]
  assert run_sql_ops
  create_role_sql = run_sql_ops[0].sql
  assert "CREATE ROLE statsro LOGIN PASSWORD 'statsro'" in create_role_sql
  assert "duplicate_object" in create_role_sql


def test_0018_uses_runpython_with_noop_reverse():
  mod = importlib.import_module(
      "hpcperfstats.site.machine.migrations.0018_statsro_connect_current_database"
  )
  assert mod.Migration.dependencies == [("machine", "0017_job_plot_artifact")]
  assert len(mod.Migration.operations) == 1
  op = mod.Migration.operations[0]
  assert isinstance(op, migrations.RunPython)
  assert op.reverse_code is migrations.RunPython.noop


def test_0012_timescaledb_compression_policy_sql_and_reverse_are_defined():
  mod = importlib.import_module(
      "hpcperfstats.site.machine.migrations.0012_update_host_data_compression_policy"
  )
  assert len(mod.Migration.operations) == 1
  op = mod.Migration.operations[0]
  assert isinstance(op, migrations.RunSQL)
  assert "remove_compression_policy('host_data')" in op.sql
  assert "compress_after => INTERVAL '30d'" in op.sql
  assert "compress_after => INTERVAL '60d'" in op.reverse_sql
