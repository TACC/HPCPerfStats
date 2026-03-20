"""Unit tests for XALT ORM schema mappings.

These tests validate the Django model contract against upstream XALT table/field
names and basic type/nullability expectations. They intentionally avoid touching
the database so they can run in non-Django environments.
"""

from pathlib import Path


def test_xalt_models_schema_mapping_contract():
  """`site/xalt/models.py` should match upstream XALT MySQL schema."""
  models_path = Path(__file__).resolve().parents[1] / "site" / "xalt" / "models.py"
  content = models_path.read_text(encoding="utf-8")

  # Table names + managed=False (read-only / existing DB).
  assert 'db_table = "xalt_run"' in content
  assert 'db_table = "xalt_object"' in content
  assert 'db_table = "xalt_link"' in content
  assert "managed = False" in content

  # Core `xalt_run` fields used by the app/API.
  assert "job_id = models.CharField(max_length=64)" in content
  assert "cwd = models.CharField(max_length=1024)" in content
  assert "cmdline = models.BinaryField()" in content
  assert "probability = models.FloatField()" in content
  assert "sum_runs = models.PositiveIntegerField()" in content
  assert "sum_time = models.FloatField()" in content

  # Join + lib mapping.
  assert 'db_table = "join_run_object"' in content
  assert "date = models.DateField()" in content
  assert "timestamp = models.DateTimeField(null=True)" in content
  assert 'module_name = models.CharField(max_length=64, null=True)' in content

  # Ensure we removed fields that don't exist in upstream schema.
  assert "exit_code" not in content

