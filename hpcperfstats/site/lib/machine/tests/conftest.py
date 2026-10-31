"""Django test configuration. Only loaded when collecting/running site.lib.machine.tests."""

import os
import tempfile
from contextlib import nullcontext

import pytest


def pytest_configure(config):
  """Ensure test INI exists, set Django settings, and run django.setup()."""
  if not os.environ.get("HPCPERFSTATS_INI") or not os.path.isfile(
    os.environ.get("HPCPERFSTATS_INI", "")
  ):
    fd, path = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    with open(path, "w") as f:
      f.write(
        "[DEFAULT]\ndebug = no\nhost_name_ext = local\n"
        "restricted_queue_keywords =\nmachine = test\nserver = test\n"
        "data_dir = /tmp\nstaff_email_domain = local\ntimezone = UTC\n"
        "total_cores = 4\n"
        "engine_name = django.db.backends.postgresql\n"
        "dbname = test\nusername = u\npassword = p\nport = 5432\n"
        "host = localhost\n"
        "[PIPELINE]\narchive_dir = /tmp\nacct_path = /tmp\n"
        "daily_archive_dir = /tmp\n"
        "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
        "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
        "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
        "[OAUTH2]\nclient_id = id\nclient_key = key\n"
        "authorize_url = http://localhost\noauth_base_url = http://localhost\n"
      )
    os.environ["HPCPERFSTATS_INI"] = path
  os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

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
def _no_prewarm_worker_processes(monkeypatch):
  """Tests drive the prewarm pipeline in-thread; never start forkserver
  prewarm worker processes from a test (the production INI default is
  ``auto``). ``test_prewarm_process_pool.py`` covers the pool with fakes."""
  import hpcperfstats.dbload.lib.conf_parser as conf_parser_module

  monkeypatch.setattr(
    conf_parser_module, "get_metrics_prewarm_processes", lambda: 0
  )


@pytest.fixture(autouse=True)
def _unit_test_database_guards(request, monkeypatch):
  """Tests with ``django_db(databases=[])`` must not hit real PG cursors."""
  if not _item_blocks_default_database(request.node):
    return

  from hpcperfstats.analysis.metrics import (
    update_metrics as update_metrics_module,
  )

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
    "refresh_public_expansion_factor_artifacts_parallel",
    lambda pool, **kwargs: {
      "degraded": 0,
      "worker_exceptions": 0,
      "watchdog_timeouts": 0,
      "pending_tasks": 0,
      "tasks_completed": 0,
      "tasks_total": 0,
    },
  )
  monkeypatch.setattr(
    update_metrics_module,
    "refresh_public_expansion_factor_artifacts_safe",
    lambda: None,
  )
  monkeypatch.setattr(
    update_metrics_module,
    "run_with_db_retry",
    lambda func, **kwargs: func(),
  )
  # ``job_is_shared_node`` reads ``job_affinity``; unit tests that need a shared
  # job patch it themselves after this default.
  for shared_node_owner in (
    "hpcperfstats.analysis.metrics.lib.job_host_data_scope",
    "hpcperfstats.analysis.metrics.lib.gen.jid_table",
  ):
    monkeypatch.setattr(
      f"{shared_node_owner}.job_is_shared_node", lambda _jid: False
    )
  import hpcperfstats.dbload.lib.shutdown_utils as shutdown_utils

  monkeypatch.setattr(shutdown_utils, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics_module, "shutdown_requested", [False])


def skipped_off_compose_network(item) -> bool:
  """Return True when ``item`` carries a compose-network ``skip``/``skipif`` mark.

  The root conftest adds ``skip`` marks; modules such as
  ``test_metrics_telemetry_bounds.py`` declare their own module-level
  ``skipif``. Both name the compose network in ``reason`` and both must be
  stripped of default-database marks, or pytest-django creates the test
  database for the skipped item and every later test in the session errors.

  Args:
    item: Collected pytest item.

  Returns:
    bool: True when a ``skip`` or ``skipif`` mark mentions the compose network.
  """
  return any(
    "Compose network" in str(mark.kwargs.get("reason", ""))
    for name in ("skip", "skipif")
    for mark in item.iter_markers(name)
  )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
  """Dropped default-database marks on tests already skipped off the compose network.

  Root conftest adds ``django_db`` and then ``skip``. pytest-django still
  creates that database for every collected item, so a host run that also
  collects the skipped test fails before the unit tests start.
  """
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
    "1",
    "yes",
    "true",
  ):
    return
  for item in items:
    if not skipped_off_compose_network(item):
      continue
    item.own_markers[:] = [
      mark for mark in item.own_markers if mark.name != "django_db"
    ]
    item.add_marker(pytest.mark.django_db(databases=[]))
