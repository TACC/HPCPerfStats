"""Pytest configuration for hpcperfstats. Sets default HPCPERFSTATS_INI for unit tests; marks site.machine.tests as django_db; provides temp_ini fixture.

AI generated.
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
  """Set default INI path for tests. Django is configured only by site.machine.tests conftest.

    AI generated.
    """
  global _DEFAULT_INI
  if os.environ.get("HPCPERFSTATS_INI"):
    return
  fd, _DEFAULT_INI = tempfile.mkstemp(suffix=".ini")
  os.close(fd)
  with open(_DEFAULT_INI, "w") as f:
    f.write(
        "[DEFAULT]\ndebug = no\nhost_name_ext = local\nrestricted_queue_keywords =\n"
        "machine = test\nserver = test\ndata_dir = /tmp\nstaff_email_domain = local\n"
        "timezone = UTC\ntotal_cores = 4\n"
        "[PORTAL]\ndbname = test\nusername = u\npassword = p\nport = 5432\nhost = localhost\n"
        "archive_dir = /tmp\nacct_path = /tmp\ndaily_archive_dir = /tmp\n"
        "engine_name = django.db.backends.postgresql\n"
        "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
        "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
        "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
        "[OAUTH2]\nclient_id = id\nclient_key = key\nauthorize_url = http://localhost\n"
        "oauth_base_url = http://localhost\n")
  os.environ["HPCPERFSTATS_INI"] = _DEFAULT_INI


def pytest_unconfigure(config):
  """Remove default INI file.

    AI generated.
    """
  global _DEFAULT_INI
  if _DEFAULT_INI and os.path.exists(_DEFAULT_INI):
    try:
      os.unlink(_DEFAULT_INI)
    except Exception:
      pass


def pytest_collection_modifyitems(config, items):
  """Mark tests under site.machine.tests as django tests.

    AI generated.
    """
  for item in items:
    if "site.machine.tests" in str(item.fspath):
      item.add_marker(pytest.mark.django_db)


@pytest.fixture
def temp_ini(tmp_path):
  """Create a minimal hpcperfstats.ini for tests that need conf_parser.

    AI generated.
    """
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text("[DEFAULT]\n"
                 "debug = no\n"
                 "host_name_ext = local\n"
                 "restricted_queue_keywords = restricted\n"
                 "machine = test\n"
                 "server = test\n"
                 "data_dir = /tmp\n"
                 "staff_email_domain = local\n"
                 "timezone = UTC\n"
                 "total_cores = 4\n"
                 "[PORTAL]\n"
                 "dbname = test\n"
                 "username = u\n"
                 "password = p\n"
                 "port = 5432\n"
                 "host = localhost\n"
                 "archive_dir = /tmp\n"
                 "acct_path = /tmp\n"
                 "daily_archive_dir = /tmp\n"
                 "engine_name = django.db.backends.postgresql\n"
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
