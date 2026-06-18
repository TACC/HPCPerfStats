"""Regression tests for migration SQL/contracts that are easy to break silently."""
import importlib

from django.db import migrations


def test_0002_statsro_create_role_is_duplicate_safe():
  mod = importlib.import_module("hpcperfstats.site.lib.machine.migrations.0002_add_read_only_user")
  run_sql_ops = [op for op in mod.Migration.operations if isinstance(op, migrations.RunSQL)]
  assert run_sql_ops
  create_role_sql = run_sql_ops[0].sql
  assert "CREATE ROLE statsro LOGIN PASSWORD 'statsro'" in create_role_sql
  assert "duplicate_object" in create_role_sql


def test_0002_statsro_connect_grant_uses_runtime_database_identifier_quoting():
  mod = importlib.import_module("hpcperfstats.site.lib.machine.migrations.0002_add_read_only_user")
  run_sql_ops = [op for op in mod.Migration.operations if isinstance(op, migrations.RunSQL)]
  assert len(run_sql_ops) >= 2
  connect_sql = run_sql_ops[1].sql
  assert "current_database()" in connect_sql
  assert "format('GRANT CONNECT ON DATABASE %I TO statsro'" in connect_sql


def test_0018_uses_runpython_with_noop_reverse():
  mod = importlib.import_module(
      "hpcperfstats.site.lib.machine.migrations.0018_statsro_connect_current_database"
  )
  assert mod.Migration.dependencies == [("machine", "0017_job_plot_artifact")]
  assert len(mod.Migration.operations) == 1
  op = mod.Migration.operations[0]
  assert isinstance(op, migrations.RunPython)
  assert op.reverse_code is migrations.RunPython.noop


def test_0012_timescaledb_compression_policy_sql_and_reverse_are_defined():
  mod = importlib.import_module(
      "hpcperfstats.site.lib.machine.migrations.0012_update_host_data_compression_policy"
  )
  assert len(mod.Migration.operations) == 1
  op = mod.Migration.operations[0]
  assert isinstance(op, migrations.RunSQL)
  assert "remove_compression_policy('host_data')" in op.sql
  assert "compress_after => INTERVAL '30d'" in op.sql
  assert "compress_after => INTERVAL '60d'" in op.reverse_sql


def test_0023_host_data_compression_policy_reduced_to_8_days():
  mod = importlib.import_module(
      "hpcperfstats.site.lib.machine.migrations.0023_reduce_host_data_compression_to_8_days"
  )
  assert mod.Migration.dependencies == [("machine", "0022_job_detail_artifact")]
  assert len(mod.Migration.operations) == 1
  op = mod.Migration.operations[0]
  assert isinstance(op, migrations.RunSQL)
  assert "remove_compression_policy('host_data')" in op.sql
  assert "compress_after => INTERVAL '8d'" in op.sql
  assert "compress_after => INTERVAL '30d'" in op.reverse_sql


def test_0001_timescaledb_sql_uses_idempotent_forms():
  mod = importlib.import_module("hpcperfstats.site.lib.machine.migrations.0001_initial")
  run_sql_ops = [op for op in mod.Migration.operations if isinstance(op, migrations.RunSQL)]
  sql_text = "\n".join(op.sql for op in run_sql_ops if isinstance(op.sql, str))

  assert "DROP CONSTRAINT IF EXISTS host_data_pkey" in sql_text
  assert "create_hypertable('host_data'" in sql_text
  assert "if_not_exists => TRUE" in sql_text
  assert "add_compression_policy('host_data'" in sql_text


def test_0020_restores_host_data_time_primary_key_contract():
  mod = importlib.import_module(
      "hpcperfstats.site.lib.machine.migrations.0020_host_data_primary_key_contract"
  )
  assert mod.Migration.dependencies == [("machine", "0019_job_data_host_data_schema_json")]
  assert len(mod.Migration.operations) == 1
  op = mod.Migration.operations[0]
  assert isinstance(op, migrations.RunSQL)
  assert "ADD CONSTRAINT host_data_pkey PRIMARY KEY (time)" in op.sql
  assert "UNIQUE (time, host, type, event)" in op.sql
