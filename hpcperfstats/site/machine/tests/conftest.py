"""Django test configuration. Only loaded when collecting/running site.machine.tests. Ensures HPCPERFSTATS_INI, then sets DJANGO_SETTINGS_MODULE and runs django.setup().

"""
import os
import tempfile

import pytest


def pytest_configure(config):
  """Ensure test INI exists, set Django settings, and run django.setup().

    """
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
