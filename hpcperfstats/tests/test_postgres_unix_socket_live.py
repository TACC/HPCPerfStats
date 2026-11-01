"""Live check: compose web/pipeline reach Postgres over the shared Unix socket.

Skipped unless HPCPERFSTATS_COMPOSE_NETWORK=1 and the configured DB host is a
socket dir. Run via tests/run_db_pytest_workflow.sh.
"""

import os

import pytest

_COMPOSE = os.environ.get(
  "HPCPERFSTATS_COMPOSE_NETWORK", ""
).strip().lower() in ("1", "yes", "true")

pytestmark = pytest.mark.skipif(
  not _COMPOSE,
  reason="Requires the compose network (tests/run_db_pytest_workflow.sh)",
)


def _default_db():
  from django.db import connection

  return connection, connection.settings_dict


@pytest.mark.django_db
def test_django_default_db_session_is_unix_socket():
  connection, settings = _default_db()
  host = str(settings.get("HOST") or "")
  if not host.startswith("/"):
    pytest.skip(f"ini host={host!r} is TCP; socket wiring not in use")
  with connection.cursor() as cur:
    cur.execute("SELECT inet_client_addr() IS NULL")
    (is_socket,) = cur.fetchone()
  assert is_socket, "session must arrive over the Unix socket, not TCP"


@pytest.mark.django_db
def test_unix_socket_requires_password_like_network():
  """Regression: image default `local ... trust` skipped the password."""
  import psycopg

  _connection, settings = _default_db()
  host = str(settings.get("HOST") or "")
  if not host.startswith("/"):
    pytest.skip(f"ini host={host!r} is TCP; socket wiring not in use")
  kwargs = {
    "host": host,
    "port": str(settings.get("PORT") or "5432"),
    "user": settings["USER"],
    "dbname": settings["NAME"],
    "connect_timeout": 5,
  }
  with pytest.raises(psycopg.OperationalError, match="password"):
    psycopg.connect(password="definitely-not-the-password", **kwargs).close()
  with pytest.raises(psycopg.OperationalError, match="password"):
    psycopg.connect(password="", **kwargs).close()
  psycopg.connect(password=settings["PASSWORD"], **kwargs).close()


def test_wait_for_postgres_available_reaches_live_socket():
  from django.conf import settings as dj

  from hpcperfstats.dbload.lib.dbwait import wait_for_postgres_available

  db = dj.DATABASES["default"]
  host = str(db.get("HOST") or "")
  if not host.startswith("/"):
    pytest.skip(f"ini host={host!r} is TCP; socket wiring not in use")
  wait_for_postgres_available(host, str(db.get("PORT") or "5432"))
