"""Compose: run real ``makemigrations --check`` against a live DB.

Catches the class of drift that produced ephemeral
``0031_alter_job_plot_artifact_options_and_more`` (Meta ``managed=True``
omitted from earlier CreateModel options). Host Autodetector coverage lives in
``test_migrations.py`` (including a host ``call_command`` with history
stubbed); this module exercises the unpatched production code path.
"""
from __future__ import annotations

import os
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def _compose_network() -> bool:
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


@pytest.mark.django_db
def test_makemigrations_check_reports_no_pending_model_changes():
  """``manage.py makemigrations --check --dry-run`` must exit clean (no drift)."""
  if not _compose_network():
    pytest.skip(
        "Requires Docker Compose network (PostgreSQL at host 'db'). "
        "Run: tests/run_db_pytest_workflow.sh -- "
        "hpcperfstats/site/lib/machine/tests/test_makemigrations_check_compose.py"
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
          "(0031-class Meta options / field surprises must be reviewed "
          f"migrations, not left for production startup):\n{out.getvalue()}"
      ) from exc
  except CommandError as exc:
    raise AssertionError(
        "makemigrations --check failed "
        f"(pending drift or inconsistent history):\n{out.getvalue()}\n{exc}"
    ) from exc

  text = out.getvalue()
  assert "Migrations for" not in text, (
      "makemigrations --check would autogenerate new migration(s) "
      f"(review and commit them):\n{text}"
  )
