import configparser
import importlib

import pytest


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


def test_resolve_postgres_wait_target_raises_when_unconfigured(
  monkeypatch, tmp_path
):
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

  with pytest.raises(configparser.NoOptionError):
    resolve_postgres_wait_target()


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
  sock_dir = tmp_path / "pg"
  sock_dir.mkdir()
  server = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
  server.bind(str(sock_dir / ".s.PGSQL.5432"))
  server.listen(1)
  try:
    dbwait.wait_for_postgres_available(
      str(sock_dir), "5432", connect_timeout_seconds=1, interval_seconds=0.01
    )
  finally:
    server.close()


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
