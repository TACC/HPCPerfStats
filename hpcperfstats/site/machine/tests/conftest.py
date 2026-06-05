"""Django test configuration. Only loaded when collecting/running site.machine.tests."""

import os
import tempfile
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest


def pytest_configure(config):
  """Ensure test INI exists, set Django settings, and run django.setup()."""
  if not os.environ.get("HPCPERFSTATS_INI") or not os.path.isfile(
      os.environ.get("HPCPERFSTATS_INI", "")):
    fd, path = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    with open(path, "w") as f:
      f.write(
          "[DEFAULT]\ndebug = no\nhost_name_ext = local\n"
          "restricted_queue_keywords =\nmachine = test\nserver = test\n"
          "data_dir = /tmp\nstaff_email_domain = local\ntimezone = UTC\n"
          "total_cores = 4\n"
          "[PORTAL]\ndbname = test\nusername = u\npassword = p\nport = 5432\n"
          "host = localhost\narchive_dir = /tmp\nacct_path = /tmp\n"
          "daily_archive_dir = /tmp\nengine_name = django.db.backends.postgresql\n"
          "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
          "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
          "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
          "[OAUTH2]\nclient_id = id\nclient_key = key\n"
          "authorize_url = http://localhost\noauth_base_url = http://localhost\n")
    os.environ["HPCPERFSTATS_INI"] = path
  os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
  from hpcperfstats.django_bootstrap import ensure_django
  ensure_django()


def _item_blocks_default_database(item):
  if item.get_closest_marker("machine_unit_mock"):
    return True
  for mark in item.iter_markers("django_db"):
    dbs = mark.kwargs.get("databases")
    if dbs is not None and len(dbs) == 0:
      return True
  return False


@pytest.fixture(autouse=True)
def _unit_test_database_guards(request, monkeypatch):
  """Tests with ``django_db(databases=[])`` must not hit real PG cursors."""
  if not _item_blocks_default_database(request.node):
    return

  from hpcperfstats.analysis.metrics import update_metrics as update_metrics_module

  monkeypatch.setattr(
      update_metrics_module,
      "_pg_session_statement_timeout_for_metrics_batch",
      lambda: nullcontext(),
  )
  monkeypatch.setattr(
      update_metrics_module,
      "_pg_local_readiness_timeouts",
      lambda: nullcontext(),
  )
  monkeypatch.setattr(
      update_metrics_module,
      "_run_public_ef_artifacts_parallel_phase",
      lambda shared_pool, phase_timer: {
          "degraded": 0,
          "worker_exceptions": 0,
          "watchdog_timeouts": 0,
          "pending_tasks": 0,
          "tasks_completed": 0,
          "tasks_total": 0,
      },
  )
  monkeypatch.setattr(update_metrics_module, "run_with_db_retry", lambda func, **kwargs: func())
