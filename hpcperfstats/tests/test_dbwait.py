import importlib


def test_resolve_postgres_wait_target_prefers_postgres_host_port(monkeypatch):
  from hpcperfstats.dbload.lib.dbwait import resolve_postgres_wait_target

  monkeypatch.setenv("POSTGRES_HOST", "my-postgres-host")
  monkeypatch.setenv("POSTGRES_PORT", "6543")
  monkeypatch.delenv("DB_HOST", raising=False)
  monkeypatch.delenv("DB_PORT", raising=False)

  host, port = resolve_postgres_wait_target()
  assert host == "my-postgres-host"
  assert port == "6543"


def test_resolve_postgres_wait_target_prefers_db_host_port(monkeypatch):
  from hpcperfstats.dbload.lib.dbwait import resolve_postgres_wait_target

  monkeypatch.delenv("POSTGRES_HOST", raising=False)
  monkeypatch.delenv("POSTGRES_PORT", raising=False)
  monkeypatch.setenv("DB_HOST", "my-db-host")
  monkeypatch.setenv("DB_PORT", "6432")

  host, port = resolve_postgres_wait_target()
  assert host == "my-db-host"
  assert port == "6432"


def test_resolve_postgres_wait_target_uses_ini_when_env_missing(
  temp_ini,
  monkeypatch,
):
  from pathlib import Path

  # Ensure env overrides aren't set.
  monkeypatch.delenv("POSTGRES_HOST", raising=False)
  monkeypatch.delenv("POSTGRES_PORT", raising=False)
  monkeypatch.delenv("DB_HOST", raising=False)
  monkeypatch.delenv("DB_PORT", raising=False)

  # Update the temporary ini host/port and reload conf_parser so it reads it.
  ini_path = Path(temp_ini)
  content = ini_path.read_text()
  content = content.replace("host = localhost", "host = ini-db-host")
  content = content.replace("port = 5432", "port = 6543")
  ini_path.write_text(content)

  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)

  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)

  from hpcperfstats.dbload.lib.dbwait import resolve_postgres_wait_target

  host, port = resolve_postgres_wait_target()
  assert host == "ini-db-host"
  assert port == "6543"


def test_can_resolve_host_port_localhost():
  from hpcperfstats.dbload.lib.dbwait import can_resolve_host_port

  assert can_resolve_host_port("localhost", "5432") is True


def test_can_resolve_host_port_invalid_host():
  from hpcperfstats.dbload.lib.dbwait import can_resolve_host_port

  assert can_resolve_host_port("not-a-real-hostname.invalid", "5432") is False


def test_resolve_postgres_wait_target_defaults_to_compose_socket(
  monkeypatch, tmp_path
):
  """An ini without host/port waits on the compose PostgreSQL socket."""
  ini = tmp_path / "missing-host-port.ini"
  ini.write_text(
    "[DEFAULT]\n"
    "debug = no\n"
    "host_name_ext = local\n"
    "restricted_queue_keywords =\n"
    "machine = test\n"
    "server = test\n"
    "data_dir = /tmp\n"
    "staff_email_domain = local\n"
    "timezone = UTC\n"
    "total_cores = 4\n"
    "engine_name = django.db.backends.postgresql\n"
    "dbname = test\n"
    "username = u\n"
    "password = p\n"
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
    "oauth_base_url = http://localhost\n"
  )
  monkeypatch.setenv("HPCPERFSTATS_INI", str(ini))
  monkeypatch.delenv("POSTGRES_HOST", raising=False)
  monkeypatch.delenv("POSTGRES_PORT", raising=False)
  monkeypatch.delenv("DB_HOST", raising=False)
  monkeypatch.delenv("DB_PORT", raising=False)

  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)

  from hpcperfstats.dbload.lib.dbwait import resolve_postgres_wait_target

  assert resolve_postgres_wait_target() == ("/run/postgresql", "5432")


def test_postgres_unix_socket_path_maps_dir_to_libpq_socket_file():
  from hpcperfstats.dbload.lib.dbwait import postgres_unix_socket_path

  assert (
    postgres_unix_socket_path("/run/postgresql", "5432")
    == "/run/postgresql/.s.PGSQL.5432"
  )
  assert postgres_unix_socket_path("db", "5432") is None
  assert postgres_unix_socket_path("127.0.0.1", "5432") is None


def test_wait_for_postgres_available_unix_socket_skips_dns(
  tmp_path, monkeypatch
):
  """Regression: a socket-dir host must not go through getaddrinfo.

  ``host = /run/postgresql`` used to feed the DNS wait, which never resolves a
  path, so web/pipeline startup blocked for the DNS budget and then exited.
  """
  import socket as socket_mod

  from hpcperfstats.dbload.lib import dbwait

  def _no_dns(*_a, **_k):
    raise AssertionError("socket-dir host must not wait on DNS")

  monkeypatch.setattr(dbwait, "wait_for_host_port_resolution", _no_dns)
  # A bare AF_UNIX listener is not a ready Postgres (that is exactly the
  # init-server race); readiness itself is covered by the
  # test_postgres_socket_* tests below.
  monkeypatch.setattr(dbwait, "_postgres_accepts_connection", lambda *_a: True)
  sock_dir = tmp_path / "pg"
  sock_dir.mkdir()
  dbwait.wait_for_postgres_available(
    str(sock_dir), "5432", connect_timeout_seconds=1, interval_seconds=0.01
  )
  assert socket_mod.AF_UNIX  # socket-dir host path, no DNS lookup


def test_wait_for_postgres_available_unix_socket_retries_until_listening(
  tmp_path, monkeypatch
):
  from hpcperfstats.dbload.lib import dbwait

  calls = []

  def _fake_accepts(host, port, timeout):
    calls.append((host, port))
    return len(calls) >= 3

  monkeypatch.setattr(dbwait, "_postgres_accepts_connection", _fake_accepts)
  monkeypatch.setattr(dbwait.time, "sleep", lambda _s: None)
  dbwait.wait_for_postgres_available(str(tmp_path), "5432")
  assert calls == [(str(tmp_path), "5432")] * 3


def test_wait_for_postgres_available_tcp_waits_dns_then_connects(monkeypatch):
  from hpcperfstats.dbload.lib import dbwait

  seen = []
  monkeypatch.setattr(
    dbwait,
    "wait_for_host_port_resolution",
    lambda host, port, **kw: seen.append(("dns", host, port, kw)),
  )
  monkeypatch.setattr(
    dbwait,
    "_postgres_accepts_connection",
    lambda host, port, timeout: seen.append(("tcp", host, port)) or True,
  )
  dbwait.wait_for_postgres_available("db", "5432", dns_timeout_seconds=7)
  assert seen[0][:3] == ("dns", "db", "5432")
  assert seen[0][3]["timeout_seconds"] == 7
  assert seen[1] == ("tcp", "db", "5432")


def test_postgres_accepts_connection_false_when_socket_missing(tmp_path):
  from hpcperfstats.dbload.lib.dbwait import _postgres_accepts_connection

  assert _postgres_accepts_connection(str(tmp_path), "5432", 0.1) is False


class _FakePgConn:
  """psycopg connection stub answering ``current_setting('listen_addresses')``."""

  def __init__(self, listen_addresses):
    self.listen_addresses = listen_addresses
    self.sql = []

  def __enter__(self):
    return self

  def __exit__(self, *_exc):
    return False

  def execute(self, sql):
    self.sql.append(sql)
    return self

  def fetchone(self):
    return (self.listen_addresses,)


def _patch_socket_connect(monkeypatch, outcome):
  """Route dbwait's psycopg.connect to ``outcome`` (conn or exception)."""
  import psycopg

  from hpcperfstats.dbload.lib import conf_parser as cfg

  seen = {}

  def _connect(**kwargs):
    seen.update(kwargs)
    if isinstance(outcome, BaseException):
      raise outcome
    return outcome

  monkeypatch.setattr(psycopg, "connect", _connect)
  monkeypatch.setattr(cfg, "get_db_name", lambda: "hpcperfstats")
  monkeypatch.setattr(cfg, "get_username", lambda: "hpcperfstats")
  monkeypatch.setattr(cfg, "get_password", lambda: "pw")
  return seen


def test_postgres_socket_init_server_is_not_ready(tmp_path, monkeypatch):
  """Regression (hpcperfstats-dev, 2026-11-04): first-boot init server.

  The Postgres image entrypoint runs a temporary server with
  ``listen_addresses=''`` on the shared ``/run/postgresql`` socket while it
  initdbs, then fast-shuts it down. web passed the old raw-socket wait,
  migrated ``machine.0001`` against it, lost the connection before the
  ``django_migrations`` row was written, and crash-looped forever on
  ``relation "host_data" already exists``.
  """
  from hpcperfstats.dbload.lib import dbwait

  seen = _patch_socket_connect(monkeypatch, _FakePgConn(""))
  assert dbwait._postgres_accepts_connection(str(tmp_path), "5432", 2) is False
  assert seen["host"] == str(tmp_path)
  assert seen["port"] == "5432"
  assert seen["dbname"] == "hpcperfstats"


def test_postgres_socket_real_server_is_ready(tmp_path, monkeypatch):
  """The real server (TCP listener configured) is ready over the socket."""
  from hpcperfstats.dbload.lib import dbwait

  conn = _FakePgConn("*")
  _patch_socket_connect(monkeypatch, conn)
  assert dbwait._postgres_accepts_connection(str(tmp_path), "5432", 2) is True
  assert conn.sql == ["SELECT current_setting('listen_addresses')"]


def test_postgres_socket_connect_error_is_not_ready(tmp_path, monkeypatch):
  """Auth/db-missing/shutdown errors mid-init mean retry, not crash."""
  import psycopg

  from hpcperfstats.dbload.lib import dbwait

  _patch_socket_connect(
    monkeypatch, psycopg.OperationalError("the database system is starting up")
  )
  assert dbwait._postgres_accepts_connection(str(tmp_path), "5432", 2) is False


def test_postgres_tcp_wait_stays_raw_connect(monkeypatch):
  """TCP mode never needs credentials: the init server has no TCP listener."""
  import psycopg

  from hpcperfstats.dbload.lib import dbwait

  def _no_psycopg(**_kw):
    raise AssertionError("TCP readiness must not open a libpq session")

  monkeypatch.setattr(psycopg, "connect", _no_psycopg)
  opened = []

  class _Sock:
    def close(self):
      opened.append("closed")

  monkeypatch.setattr(
    dbwait.socket, "create_connection", lambda *_a, **_kw: _Sock()
  )
  assert dbwait._postgres_accepts_connection("db", "5432", 2) is True
  assert opened == ["closed"]
