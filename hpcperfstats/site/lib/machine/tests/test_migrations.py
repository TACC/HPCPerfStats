"""Regression tests for migration SQL/contracts that are easy to break silently."""
import importlib

import pytest
from django.db import migrations

# Importlib / source-contract checks — no live PostgreSQL required on the host.
pytestmark = pytest.mark.machine_unit_mock


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
  """0001 must CREATE EXTENSION before create_hypertable (PG18 fresh-DB bake failure)."""
  mod = importlib.import_module("hpcperfstats.site.lib.machine.migrations.0001_initial")
  run_sql_ops = [op for op in mod.Migration.operations if isinstance(op, migrations.RunSQL)]
  sql_text = "\n".join(op.sql for op in run_sql_ops if isinstance(op.sql, str))

  assert "CREATE EXTENSION IF NOT EXISTS timescaledb" in sql_text
  assert sql_text.index("CREATE EXTENSION IF NOT EXISTS timescaledb") < sql_text.index(
      "create_hypertable('host_data'"
  )
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


def _0030_ops():
  mod = importlib.import_module(
      "hpcperfstats.site.lib.machine.migrations.0030_host_data_unique_include_dev"
  )
  assert mod.Migration.dependencies == [("machine", "0029_proc_data_host_proc_fields")]
  assert mod.Migration.atomic is False
  assert len(mod.Migration.operations) == 1
  sep = mod.Migration.operations[0]
  assert isinstance(sep, migrations.SeparateDatabaseAndState)
  return sep


def test_0030_has_no_unbounded_host_data_update():
  """Phase 1 must not full-table UPDATE host_data (statement_timeout crash loop)."""
  sep = _0030_ops()
  sql_blobs = []
  for op in sep.database_operations:
    if isinstance(op, migrations.RunSQL) and isinstance(op.sql, str):
      sql_blobs.append(op.sql)
  joined = "\n".join(sql_blobs).lower()
  assert "update host_data" not in joined
  assert "set dev" not in joined


def test_0030_removes_compression_policy():
  sep = _0030_ops()
  sql_blobs = [
      op.sql
      for op in sep.database_operations
      if isinstance(op, migrations.RunSQL) and isinstance(op.sql, str)
  ]
  joined = "\n".join(sql_blobs)
  assert "remove_compression_policy('host_data'" in joined
  assert "if_exists" in joined


def test_0030_does_not_add_unique_dev_constraint():
  """Phase 1 must not ADD CONSTRAINT / decompress — that is Phase 2 after decompress."""
  sep = _0030_ops()
  sql_blobs = [
      op.sql
      for op in sep.database_operations
      if isinstance(op, migrations.RunSQL) and isinstance(op.sql, str)
  ]
  joined = "\n".join(sql_blobs).lower()
  assert "add constraint" not in joined
  assert "host_data_time_host_type_event_dev_key" not in joined
  assert "decompress_chunk" not in joined
  assert "unique (time, host, type, event, dev)" not in joined


def test_0030_keeps_state_unique_together():
  sep = _0030_ops()
  state_ops = sep.state_operations
  assert len(state_ops) == 1
  assert isinstance(state_ops[0], migrations.AlterUniqueTogether)
  assert state_ops[0].name == "host_data"
  assert state_ops[0].unique_together == {("time", "host", "type", "event", "dev")}


def test_0031_is_state_only():
  """Reviewed drift migration must be AlterModelOptions only — no DDL on large tables."""
  mod = importlib.import_module(
      "hpcperfstats.site.lib.machine.migrations.0031_alter_job_plot_artifact_options_and_more"
  )
  assert mod.Migration.dependencies == [("machine", "0030_host_data_unique_include_dev")]
  ops = mod.Migration.operations
  assert len(ops) == 2
  for op in ops:
    assert isinstance(op, migrations.AlterModelOptions)
  names = {op.name for op in ops}
  assert names == {"job_plot_artifact", "public_metrics_artifact"}
  for op in ops:
    assert op.options == {"managed": True}
  forbidden = (
      migrations.RunSQL,
      migrations.AddConstraint,
      migrations.AddField,
      migrations.AlterField,
      migrations.CreateModel,
      migrations.DeleteModel,
  )
  assert not any(isinstance(op, forbidden) for op in ops)


def _0032_sql():
  mod = importlib.import_module(
      "hpcperfstats.site.lib.machine.migrations.0032_host_data_unique_include_dev_db"
  )
  assert mod.Migration.dependencies == [
      ("machine", "0031_alter_job_plot_artifact_options_and_more")
  ]
  assert mod.Migration.atomic is False
  assert len(mod.Migration.operations) == 1
  op = mod.Migration.operations[0]
  assert isinstance(op, migrations.RunSQL)
  assert isinstance(op.sql, str)
  assert op.reverse_sql is migrations.RunSQL.noop
  return op.sql


def test_0032_sets_statement_timeout_zero():
  sql = _0032_sql()
  assert "SET statement_timeout = 0" in sql


def test_0032_skips_when_compressed_chunks_present():
  sql = _0032_sql().lower()
  assert "is_compressed" in sql
  assert "raise notice" in sql
  assert "skipping" in sql
  assert "compressed" in sql


def test_0032_does_not_drop_primary_key():
  """Operator owns hpcperfstats02 PK normalize; migrate must not DROP PK."""
  sql = _0032_sql()
  sql_l = sql.lower()
  assert "drop constraint host_data_pkey" not in sql_l
  assert "host_data_pkey" not in sql_l
  assert "multi_col_pk" in sql_l
  assert "contype = 'p'" in sql_l
  # UNIQUE drops use discovered conname via format(... %I), not a hard-coded PK.
  assert "DROP CONSTRAINT %I" in sql or "drop constraint %i" in sql_l


def test_0032_drops_four_col_unique_by_columns():
  sql = _0032_sql()
  assert "ARRAY['time', 'host', 'type', 'event']::text[]" in sql
  assert "ARRAY['time', 'host', 'type', 'event', 'dev']::text[]" in sql
  assert "host_data_time_host_type_event_dev_uniq" in sql
  assert "UNIQUE (time, host, type, event, dev)" in sql
  assert "DROP CONSTRAINT" in sql.upper() or "drop constraint" in sql.lower()


def test_0032_restores_compression_policy_8d():
  sql = _0032_sql()
  assert "add_compression_policy" in sql
  assert "compress_after => INTERVAL '8d'" in sql


def test_0032_coalesces_null_dev():
  sql = _0032_sql().lower()
  assert "update host_data set dev = '' where dev is null" in sql


def test_no_pending_model_migrations():
  """Host Autodetector: no model/migration drift (0031-class Meta surprises)."""
  from django.apps import apps
  from django.db.migrations.autodetector import MigrationAutodetector
  from django.db.migrations.loader import MigrationLoader
  from django.db.migrations.state import ProjectState

  loader = MigrationLoader(None, ignore_no_migrations=True)
  changes = MigrationAutodetector(
      loader.project_state(),
      ProjectState.from_apps(apps),
  ).changes(graph=loader.graph)
  assert changes == {}, f"Pending model/migration drift: {sorted(changes)}"


def test_makemigrations_check_command_reports_no_changes(monkeypatch):
  """Run real ``makemigrations --check --dry-run`` on the host.

  Production used to call this at startup and silently emit Meta-option drift
  (``0031_alter_job_plot_artifact_options_and_more``). ``check_consistent_history``
  needs a live DB; stub it so the command's Autodetector path still runs.
  Compose re-runs the unpatched command in ``test_makemigrations_check_compose.py``.
  """
  from io import StringIO

  from django.core.management import call_command
  from django.core.management.base import CommandError
  from django.db.migrations.loader import MigrationLoader

  monkeypatch.setattr(
      MigrationLoader,
      "check_consistent_history",
      lambda self, connection: None,
  )

  out = StringIO()
  try:
    call_command(
        "makemigrations",
        "--check",
        "--dry-run",
        stdout=out,
        stderr=out,
    )
  except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
    if code != 0:
      raise AssertionError(
          "makemigrations --check found pending model/migration drift "
          f"(0031-class Meta / field surprises):\n{out.getvalue()}"
      ) from exc
  except CommandError as exc:
    raise AssertionError(
        f"makemigrations --check failed:\n{out.getvalue()}\n{exc}"
    ) from exc

  text = out.getvalue()
  assert "Migrations for" not in text, (
      "makemigrations --check would autogenerate new migration(s):\n" + text
  )
