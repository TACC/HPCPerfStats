"""Pytest configuration for hpcperfstats. Sets default HPCPERFSTATS_INI for unit tests; marks site/lib/machine/tests with django_db; provides temp_ini fixture.

"""
import os
import sys
import tempfile

import pytest

# Ensure package root is on path when running pytest from repo root
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
  sys.path.insert(0, os.path.dirname(_here))

# Default INI for tests so conf_parser can be imported without missing file
_DEFAULT_INI = None


def pytest_configure(config):
  """Set default INI path for tests. Django is configured by site/lib/machine/tests conftest when loaded."""
  global _DEFAULT_INI
  # macOS: Homebrew GNU findutils provides ``gfind`` with -printf (BSD find does not).
  import shutil

  gfind = shutil.which("gfind")
  if gfind:
    os.environ.setdefault("HPCPERFSTATS_FIND_BIN", gfind)
  if os.environ.get("HPCPERFSTATS_INI"):
    return
  fd, _DEFAULT_INI = tempfile.mkstemp(suffix=".ini")
  os.close(fd)
  with open(_DEFAULT_INI, "w") as f:
    f.write(
        "[DEFAULT]\ndebug = no\nhost_name_ext = local\nrestricted_queue_keywords =\n"
        "machine = test\nserver = test\ndata_dir = /tmp\nstaff_email_domain = local\n"
        "timezone = UTC\ntotal_cores = 4\n"
        "engine_name = django.db.backends.sqlite3\n"
        "dbname = test\nusername = u\npassword = p\nport = 5432\nhost = localhost\n"
        "[PIPELINE]\narchive_dir = /tmp\nacct_path = /tmp\ndaily_archive_dir = /tmp\n"
        "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
        "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
        "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
        "[OAUTH2]\nclient_id = id\nclient_key = key\nauthorize_url = http://localhost\n"
        "oauth_base_url = http://localhost\n")
  os.environ["HPCPERFSTATS_INI"] = _DEFAULT_INI


def pytest_unconfigure(config):
  """Remove default INI file.

    """
  global _DEFAULT_INI
  if _DEFAULT_INI and os.path.exists(_DEFAULT_INI):
    try:
      os.unlink(_DEFAULT_INI)
    except Exception:
      pass


def _compose_network_enabled():
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _django_db_needs_default_database(item):
  """True if any django_db marker allows use of the default database."""
  marks = list(item.iter_markers("django_db"))
  if not marks:
    return False
  for m in marks:
    if "databases" not in m.kwargs:
      return True
    dbs = m.kwargs.get("databases")
    if dbs is None:
      return True
    if len(dbs) > 0:
      return True
  return False


def pytest_collection_modifyitems(config, items):
  """Mark site/lib/machine/tests with django_db when missing; skip Postgres-backed tests off compose.

  DB- and Redis-backed integration runs set HPCPERFSTATS_COMPOSE_NETWORK=1 (see
  tests/run_db_pytest_workflow.sh and tests/run_redis_cache_pytest_workflow.sh).
  Tests that only need Django settings + mocks use ``django_db(databases=[])``
  so they still run on the host.
  """
  for item in items:
    path = str(item.fspath).replace("\\", "/")
    if "/site/lib/machine/tests/" in path and not list(item.iter_markers("django_db")):
      if item.get_closest_marker("machine_unit_mock"):
        item.add_marker(pytest.mark.django_db(databases=[]))
        continue
      item.add_marker(pytest.mark.django_db)

  # Unit tests under hpcperfstats/tests/ import Django models or call
  # close_old_connections. On the host use django_db(databases=[]) (mocks only).
  # Under compose, allow the default database for integration-style dbload tests.
  for item in items:
    path = str(item.fspath).replace("\\", "/")
    if "/hpcperfstats/tests/" not in path:
      continue
    if list(item.iter_markers("django_db")):
      continue
    if item.get_closest_marker("machine_unit_mock"):
      item.add_marker(pytest.mark.django_db(databases=[]))
      continue
    if _compose_network_enabled():
      item.add_marker(pytest.mark.django_db)
    else:
      item.add_marker(pytest.mark.django_db(databases=[]))

  if _compose_network_enabled():
    return

  skip_compose = pytest.mark.skip(
      reason=(
          "Requires Docker Compose network (PostgreSQL at host 'db'). "
          "Run: tests/run_db_pytest_workflow.sh"
      ),
  )
  for item in items:
    path = str(item.fspath).replace("\\", "/")
    if "/site/lib/machine/tests/" not in path:
      continue
    if item.get_closest_marker("machine_unit_mock"):
      continue
    if not _django_db_needs_default_database(item):
      continue
    item.add_marker(skip_compose)


@pytest.fixture(autouse=True)
def _archive_members_store_test_policy(tmp_path_factory):
  """Install a process-wide members store for sync_timedb unit tests."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
      SyncTimedbArchiveMembersStore,
      set_process_archive_members_store,
  )

  store = SyncTimedbArchiveMembersStore(
      str(tmp_path_factory.mktemp("archive_members_store")),
  )
  set_process_archive_members_store(store)
  yield store
  set_process_archive_members_store(None)


@pytest.fixture
def temp_ini(tmp_path):
  """Create a minimal hpcperfstats.ini for tests that need conf_parser.

    """
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text("[DEFAULT]\n"
                 "debug = no\n"
                 "secret_key = test-secret-key-do-not-use-in-production\n"
                 "host_name_ext = local\n"
                 "restricted_queue_keywords = restricted\n"
                 "machine = test\n"
                 "server = test\n"
                 "data_dir = /tmp\n"
                 "staff_email_domain = local\n"
                 "timezone = UTC\n"
                 "total_cores = 4\n"
                 "engine_name = django.db.backends.sqlite3\n"
                 "dbname = test\n"
                 "username = u\n"
                 "password = p\n"
                 "port = 5432\n"
                 "host = localhost\n"
                 "[PIPELINE]\n"
                 "archive_dir = /tmp\n"
                 "acct_path = /tmp\n"
                 "daily_archive_dir = /tmp\n"
                 "[RMQ]\n"
                 "rmq_server = localhost\n"
                 "rmq_queue = test\n"
                 "[XALT]\n"
                 "xalt_engine = django.db.backends.sqlite3\n"
                 "xalt_name = xalt\n"
                 "xalt_user = u\n"
                 "xalt_password = p\n"
                 "xalt_host = localhost\n"
                 "[OAUTH2]\n"
                 "client_id = id\n"
                 "client_key = key\n"
                 "authorize_url = http://localhost\n"
                 "oauth_base_url = http://localhost\n")
  return str(ini)
