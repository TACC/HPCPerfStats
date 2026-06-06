import importlib
import configparser
import pytest


def test_resolve_postgres_wait_target_prefers_postgres_host_port(monkeypatch):
  from hpcperfstats.dbwait import resolve_postgres_wait_target

  monkeypatch.setenv("POSTGRES_HOST", "my-postgres-host")
  monkeypatch.setenv("POSTGRES_PORT", "6543")
  monkeypatch.delenv("DB_HOST", raising=False)
  monkeypatch.delenv("DB_PORT", raising=False)

  host, port = resolve_postgres_wait_target()
  assert host == "my-postgres-host"
  assert port == "6543"


def test_resolve_postgres_wait_target_prefers_db_host_port(monkeypatch):
  from hpcperfstats.dbwait import resolve_postgres_wait_target

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

  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)

  from hpcperfstats.dbwait import resolve_postgres_wait_target

  host, port = resolve_postgres_wait_target()
  assert host == "ini-db-host"
  assert port == "6543"


def test_can_resolve_host_port_localhost():
  from hpcperfstats.dbwait import can_resolve_host_port

  assert can_resolve_host_port("localhost", "5432") is True


def test_can_resolve_host_port_invalid_host():
  from hpcperfstats.dbwait import can_resolve_host_port

  assert (
    can_resolve_host_port("not-a-real-hostname.invalid", "5432")
    is False
  )


def test_resolve_postgres_wait_target_raises_when_unconfigured(monkeypatch, tmp_path):
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

  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)

  from hpcperfstats.dbwait import resolve_postgres_wait_target

  with pytest.raises(configparser.NoOptionError):
    resolve_postgres_wait_target()

